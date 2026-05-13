"""Cross-modality provider routing for v0.5.0.

Implements REQ-VG-ROUTE-002. Single entry point ``route_session``
runs once per session at session-create time and picks the
(stt, llm, tts) triple from the project's rosters that minimises
predicted total response latency under the configured budget.

Inputs:

* ``project_config.routing.budget_ms`` — the latency budget (OQ1
  default 1500 ms).
* ``project_config.routing.rosters`` — ordered allow-list of
  provider ids per modality.
* ``project_config.routing.fallback_to_fastest`` — when ``True``,
  no-fit triggers the fastest-available + ``budget_overrun=True``
  path (AC-2); when ``False``, raises ``BudgetExceeded``.
* ``latency_observations_repo.get_for_project`` — recent observed
  p50 per (provider, modality). Roll-up worker (T06) refreshes
  every 15 minutes (OQ2).
* Caller overrides — explicit ``{modality: provider}`` map; the
  router respects them and only picks for the unset modalities
  (AC-3).
* ``provider_baselines.json`` — curated published-median latencies
  per (provider, modality). Loaded from
  ``voicegateway.core.provider_baselines`` (T07). When neither an
  observation nor a baseline exists for a candidate, the candidate
  is skipped (AC-4 fallback).

Output: :class:`RoutedTriple` with the picked stt/llm/tts ids,
the summed predicted latency, and a ``budget_overrun`` boolean.

The triple stays fixed for the session (AC-5): the router has no
mid-call surface.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from importlib import resources
from typing import TYPE_CHECKING

from voicegateway.storage import latency_observations_repo

if TYPE_CHECKING:
    import aiosqlite

    from voicegateway.core.config import ProjectConfig

logger = logging.getLogger(__name__)


_MODALITIES = ("stt", "llm", "tts")


class BudgetExceeded(Exception):
    """Raised when no candidate triple fits and fallback is disabled."""


@dataclass(frozen=True)
class RoutedTriple:
    """Result of :func:`route_session`.

    ``predicted_ms`` is the sum of the per-modality predictions used
    to pick this triple (observed p50 when available, baseline
    median otherwise). It is the router's best guess; the session's
    actual end-to-end latency is recorded as ``sessions.budget_ms_used``
    after the call closes.

    ``budget_overrun`` is True when the picked triple's predicted
    total exceeds the project's ``budget_ms`` (the fallback path),
    False when the triple fits.
    """

    stt: str
    llm: str
    tts: str
    predicted_ms: int
    budget_overrun: bool


_baselines_cache: dict[tuple[str, str], int] | None = None


def _load_baselines() -> dict[tuple[str, str], int]:
    """Load provider_baselines.json from voicegateway.core.

    Returns an empty dict when the file is absent so the router stays
    importable before T07 ships the canonical baselines. Result is
    cached at module level on first call.
    """
    global _baselines_cache  # noqa: PLW0603
    if _baselines_cache is not None:
        return _baselines_cache

    try:
        raw = (
            resources.files("voicegateway.core")
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
    db: aiosqlite.Connection,
    *,
    project_id: str,
    project_config: ProjectConfig,
    caller_overrides: dict[str, str] | None = None,
) -> RoutedTriple:
    """Pick the (stt, llm, tts) triple for a new session.

    Implements REQ-VG-ROUTE-002. Caller-overrides win over roster
    picks (AC-3). When at least one candidate triple has a
    prediction <= budget_ms, the lowest-predicted-total triple is
    returned with ``budget_overrun=False``. When nothing fits, the
    fastest available triple is returned with
    ``budget_overrun=True`` (AC-2) provided ``fallback_to_fastest``
    is enabled; otherwise :class:`BudgetExceeded` is raised.
    """
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

    # Observed p50 per (provider, modality).
    obs_rows = await latency_observations_repo.get_for_project(db, project_id)
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
