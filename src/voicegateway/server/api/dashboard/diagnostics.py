"""In-memory LiveKit diagnostics runs for the OSS dashboard.

Single local vantage: creds come from env / voicegw.yaml via resolve_creds
(read-only, never persisted), checks run as a background asyncio task, and
runs live in a process-local dict (ephemeral, lost on restart). The engine
probes are imported lazily inside livekit_diag.service so a missing livekit
dependency degrades to a failed check, never a dead dashboard.

Every endpoint is gated behind require_scope(ADMIN_SCOPE): a run can place
billed calls and touch shared LiveKit infrastructure, so it needs the admin
role. That gate is a no-op when no API keys are configured (the local
single-operator default), and enforces the admin scope once auth is enabled.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from voicegateway.core.auth import ADMIN_SCOPE
from voicegateway.livekit_diag import service
from voicegateway.livekit_diag.config import CredsError, resolve_creds
from voicegateway.server.api._deps import get_gateway, require_scope

if TYPE_CHECKING:  # pragma: no cover - typing only
    from voicegateway.core.gateway import Gateway

router = APIRouter(prefix="/diagnostics", tags=["dashboard"])

_VALID_CHECKS = {"agents", "sfu", "sfu_load", "latency"}
_HISTORY_CAP = 20
_OVERALL_RUN_TIMEOUT_SECONDS = 360.0

_RUNS: dict[str, _Run] = {}
_ORDER: list[str] = []  # run_ids, newest last
_TASKS: set[asyncio.Task[None]] = set()


@dataclass
class _Run:
    run_id: str
    checks: list[str]
    config: dict[str, Any]
    status: str = "queued"
    results: dict[str, Any] | None = None
    verdict: str | None = None
    error: str | None = None
    created_at: str = field(default_factory=str)
    started_at: str | None = None
    ended_at: str | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _as_dict(r: _Run) -> dict[str, Any]:
    return {
        "run_id": r.run_id,
        "status": r.status,
        "checks": r.checks,
        "config": r.config,
        "results": r.results,
        "verdict": r.verdict,
        "error": r.error,
        "created_at": r.created_at,
        "started_at": r.started_at,
        "ended_at": r.ended_at,
    }


# Seams for tests to override (monkeypatch):
def _make_probes(store: Any) -> service.RealProbes:
    """Probes bound to this host's telemetry store.

    ``store`` is what lets the latency check report the STT/LLM/TTS split rather
    than end-to-end time alone: the split lives in the rows the probed agent
    wrote for the probe's room, so without a store to read them back the check
    can only time the reply from outside. None (storage disabled, or an agent
    reporting to a remote collector) degrades to end-to-end only.
    """
    return service.RealProbes(store)


def _resolve_creds() -> Any:
    return resolve_creds(None, None, None)


def _active_run_id() -> str | None:
    for rid in reversed(_ORDER):
        if _RUNS[rid].status in ("queued", "running"):
            return rid
    return None


async def _execute(run: _Run, creds: Any, store: Any = None) -> None:
    run.status = "running"
    run.started_at = _now()
    try:
        out = await asyncio.wait_for(
            service.execute_run(
                run.checks, run.config, creds, probes=_make_probes(store)
            ),
            _OVERALL_RUN_TIMEOUT_SECONDS,
        )
        run.results = out
        run.verdict = out.get("verdict")
        run.status = "done"
    except TimeoutError:
        run.status = "failed"
        run.error = "run timed out"
    except Exception as exc:  # noqa: BLE001 - a run must never crash the loop
        run.status = "failed"
        run.error = str(exc)
    finally:
        run.ended_at = _now()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class _RunRequest(BaseModel):
    checks: list[str]
    config: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/creds")
async def get_creds(
    _auth: None = Depends(require_scope(ADMIN_SCOPE)),
) -> dict[str, Any]:
    """Return whether LiveKit credentials are configured and the server URL."""
    try:
        creds = _resolve_creds()
        return {"configured": True, "url": creds.url}
    except CredsError:
        return {"configured": False, "url": None}


@router.post("/runs")
async def create_run(
    body: _RunRequest,
    _auth: None = Depends(require_scope(ADMIN_SCOPE)),
    gateway: Gateway = Depends(get_gateway),
) -> dict[str, Any]:
    """Start a new diagnostics run. Returns 400 when not configured or checks invalid,
    409 when another run is already active."""
    valid = set(body.checks) & _VALID_CHECKS
    if not body.checks or not valid:
        raise HTTPException(
            status_code=400,
            detail=(
                "checks must be a non-empty subset of: "
                + ", ".join(sorted(_VALID_CHECKS))
            ),
        )

    try:
        creds = _resolve_creds()
    except CredsError:
        raise HTTPException(status_code=400, detail="LiveKit not configured") from None

    if _active_run_id() is not None:
        raise HTTPException(
            status_code=409, detail="a diagnostics run is already in progress"
        )

    run = _Run(
        run_id=uuid.uuid4().hex,
        checks=sorted(valid),
        config=service.clamp_config(body.config or {}),
        created_at=_now(),
    )
    _RUNS[run.run_id] = run
    _ORDER.append(run.run_id)

    # Trim history: drop oldest terminal runs beyond the cap.
    while len(_ORDER) > _HISTORY_CAP:
        candidate = _ORDER[0]
        if _RUNS[candidate].status in ("done", "failed"):
            _ORDER.pop(0)
            del _RUNS[candidate]
        else:
            break

    task = asyncio.create_task(_execute(run, creds, gateway.storage))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)

    return {"run_id": run.run_id, "status": "queued"}


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    _auth: None = Depends(require_scope(ADMIN_SCOPE)),
) -> dict[str, Any]:
    """Return a single run record by id, or 404."""
    run = _RUNS.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    return _as_dict(run)


@router.get("/runs")
async def list_runs(
    _auth: None = Depends(require_scope(ADMIN_SCOPE)),
) -> list[dict[str, Any]]:
    """Return the run history, newest first, capped at 20."""
    return [_as_dict(_RUNS[r]) for r in reversed(_ORDER)]
