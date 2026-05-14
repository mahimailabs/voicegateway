"""Cross-modality provider routing."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from importlib import resources
from typing import TYPE_CHECKING

from voicegateway.repository import (
    latency_observations_repository as latency_observations,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from voicegateway.core.config import ProjectConfig

logger = logging.getLogger(__name__)


_MODALITIES = ("stt", "llm", "tts")


class BudgetExceeded(Exception):
    """Raised when no candidate triple fits and fallback is disabled."""


@dataclass(frozen=True)
class RoutedTriple:
    """Result of :func:`route_session`."""

    stt: str
    llm: str
    tts: str
    predicted_ms: int
    budget_overrun: bool


_baselines_cache: dict[tuple[str, str], int] | None = None


def _load_baselines() -> dict[tuple[str, str], int]:
    """Load provider_baselines.json from voicegateway.core."""
    global _baselines_cache  # noqa: PLW0603
    if _baselines_cache is not None:
        return _baselines_cache

    try:
        raw = (
            resources.files("voicegateway.data")
            .joinpath("provider_baselines.json")
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        _baselines_cache = {}
        return _baselines_cache

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(
            "provider_baselines.json failed to parse; routing fallback disabled"
        )
        _baselines_cache = {}
        return _baselines_cache

    entries: dict[tuple[str, str], int] = {}
    for entry in data.get("baselines", []):
        try:
            provider = str(entry["provider"])
            modality = str(entry["modality"])
            median_ms = int(entry["median_ms"])
        except (KeyError, TypeError, ValueError):
            continue
        entries[(provider, modality)] = median_ms
    _baselines_cache = entries
    return _baselines_cache


def _reset_baselines_cache() -> None:
    """Test helper: drop the module-level cache so tests can rewire."""
    global _baselines_cache  # noqa: PLW0603
    _baselines_cache = None


async def route_session(
    db: AsyncSession,
    *,
    project_id: str,
    project_config: ProjectConfig,
    caller_overrides: dict[str, str] | None = None,
) -> RoutedTriple:
    """Pick the (stt, llm, tts) triple for a new session."""
    overrides = dict(caller_overrides or {})
    for modality in overrides:
        if modality not in _MODALITIES:
            raise ValueError(
                f"unknown override modality {modality!r}; expected one of {_MODALITIES}"
            )

    routing_cfg = project_config.routing
    budget_ms = routing_cfg.budget_ms
    rosters = routing_cfg.rosters

    # Build per-modality candidate lists.
    def _candidates(modality: str) -> list[str]:
        if modality in overrides:
            return [overrides[modality]]
        roster = rosters.get(modality) or []
        return [str(p) for p in roster]

    by_modality: dict[str, list[str]] = {m: _candidates(m) for m in _MODALITIES}
    missing = [m for m, c in by_modality.items() if not c]
    if missing:
        raise ValueError(
            f"project {project_id!r} has empty roster(s) for {missing}; cannot route"
        )

    obs_rows = await latency_observations.get_for_project(db, project_id)
    observed: dict[tuple[str, str], int] = {
        (r.provider, r.modality): r.p50_ms for r in obs_rows if r.p50_ms is not None
    }
    baselines = _load_baselines()

    def _predict(provider: str, modality: str) -> int | None:
        if (provider, modality) in observed:
            return observed[(provider, modality)]
        if (provider, modality) in baselines:
            return baselines[(provider, modality)]
        return None

    best_under_budget: tuple[int, str, str, str] | None = None
    best_overall: tuple[int, str, str, str] | None = None
    considered = 0
    for stt in by_modality["stt"]:
        for llm in by_modality["llm"]:
            for tts in by_modality["tts"]:
                p_stt = _predict(stt, "stt")
                p_llm = _predict(llm, "llm")
                p_tts = _predict(tts, "tts")
                if p_stt is None or p_llm is None or p_tts is None:
                    continue
                total = p_stt + p_llm + p_tts
                cand = (total, stt, llm, tts)
                considered += 1
                if best_overall is None or cand < best_overall:
                    best_overall = cand
                if total <= budget_ms and (
                    best_under_budget is None or cand < best_under_budget
                ):
                    best_under_budget = cand

    if best_under_budget is not None:
        total, stt, llm, tts = best_under_budget
        return RoutedTriple(
            stt=stt, llm=llm, tts=tts, predicted_ms=total, budget_overrun=False
        )

    if best_overall is None:
        raise ValueError(
            f"no candidate triple has predictions for project "
            f"{project_id!r}; populate latency_observations or extend "
            "provider_baselines.json"
        )

    if not routing_cfg.fallback_to_fastest:
        total, stt, llm, tts = best_overall
        raise BudgetExceeded(
            f"no triple fits budget_ms={budget_ms} for project "
            f"{project_id!r}; fastest available was {stt}/{llm}/{tts} "
            f"at {total} ms"
        )

    total, stt, llm, tts = best_overall
    logger.info(
        "route_session: project=%s budget_ms=%d fell back to fastest "
        "triple %s/%s/%s at predicted %d ms (overrun)",
        project_id,
        budget_ms,
        stt,
        llm,
        tts,
        total,
    )
    return RoutedTriple(
        stt=stt, llm=llm, tts=tts, predicted_ms=total, budget_overrun=True
    )


__all__ = [
    "BudgetExceeded",
    "RoutedTriple",
    "route_session",
]
