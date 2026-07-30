"""LiveKit diagnostics runs for the OSS dashboard, persisted to storage.

Single local vantage: creds come from env / voicegw.yaml via resolve_creds
(read-only, never persisted) and checks run as a background asyncio task. The
engine probes are imported lazily inside livekit_diag.service so a missing
livekit dependency degrades to a failed check, never a dead dashboard.

**Where a run lives.** Every state transition is written to the
``diagnostics_runs`` table (queued -> running -> done/failed), so the history
survives a restart: a run places real, billed calls, and used to be the least
durable thing this dashboard produced. ``_RUNS``/``_ORDER`` remain as the
in-process working set -- the live, mutating record of a run this process is
executing, and the whole story when storage is disabled. Reads prefer the
in-memory copy (it is the freshest) and fall back to the table.

**What stays process-local on purpose.** ``_active_run_id`` only ever consults
memory. An "active" run is a live asyncio task in this process; a row left at
``running`` by a killed process is a corpse, and treating it as active would 409
every future run forever.

The endpoint payloads are unchanged by persistence: :func:`_as_dict` is the
single response formatter, and a stored row is rehydrated into the same ``_Run``
before being served, so a persisted run cannot drift from an in-memory one.

Every endpoint is gated behind require_scope(ADMIN_SCOPE): a run can place
billed calls and touch shared LiveKit infrastructure, so it needs the admin
role. That gate is a no-op when no API keys are configured (the local
single-operator default), and enforces the admin scope once auth is enabled.
"""

from __future__ import annotations

import asyncio
import logging
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

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/diagnostics", tags=["dashboard"])

_VALID_CHECKS = {"agents", "sfu", "sfu_load", "latency"}
# Bounds the in-process working set only. The stored history is not capped (it
# ages out on the retention pass), which is the point of persisting runs.
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
    """The one response formatter. Stored runs are rehydrated into a ``_Run``
    (:func:`_run_from_row`) and served through here too, so a persisted run
    cannot grow a different payload from an in-memory one."""
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


def _run_from_row(row: dict[str, Any]) -> _Run:
    """Rehydrate a stored ``diagnostics_runs`` row into a ``_Run``.

    ``project`` is dropped on purpose: it exists so a run ages out on the
    per-project retention pass and has never been part of this payload.
    """
    return _Run(
        run_id=row["run_id"],
        checks=row["checks"],
        config=row["config"],
        status=row["status"],
        results=row["results"],
        verdict=row["verdict"],
        error=row["error"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
    )


async def _persist(store: Any, run: _Run) -> None:
    """Write ``run``'s current state to storage. Never raises into the caller.

    ``store`` is None when cost-tracking storage is disabled, and then a run
    lives in memory exactly as it did before this table existed. A write that
    fails is logged and swallowed on purpose: a probe run already in flight must
    not die because the database is busy, and the POST that started it must not
    500 after the run was accepted and the task spawned.
    """
    if store is None:
        return
    try:
        await store.upsert_diagnostics_run(
            run_id=run.run_id,
            checks=run.checks,
            config=run.config,
            status=run.status,
            results=run.results,
            verdict=run.verdict,
            error=run.error,
            created_at=run.created_at,
            started_at=run.started_at,
            ended_at=run.ended_at,
        )
    except Exception:  # noqa: BLE001 - persistence must not break a live run
        _logger.warning(
            "could not persist diagnostics run %s (status=%s); "
            "it stays in this process only",
            run.run_id,
            run.status,
            exc_info=True,
        )


async def _load_run(store: Any, run_id: str) -> _Run | None:
    """One stored run, or None when it is absent (or storage is disabled).

    Read errors are deliberately NOT swallowed: answering 404 because the
    database is unreachable would report "no such run" for a run that exists.
    """
    if store is None:
        return None
    row = await store.get_diagnostics_run(run_id)
    return None if row is None else _run_from_row(row)


async def _history(store: Any) -> list[_Run]:
    """The run history, newest first, merged across storage and this process.

    The in-process copy of a run wins on overlap: a status flip is persisted
    immediately after it happens, not atomically with it, so memory is the
    fresher of the two. A run that memory has and the table does not (storage
    disabled, or a write that failed) is folded in rather than dropped, which is
    why the result can exceed the stored read limit by up to ``_HISTORY_CAP``.
    """
    stored = await _load_history(store)
    if not stored:
        # Nothing persisted (storage disabled, or no run has been recorded on
        # this database yet): the working set is the whole history, in exactly
        # the order this endpoint has always returned it.
        return [_RUNS[rid] for rid in reversed(_ORDER)]
    merged: dict[str, _Run] = {r.run_id: r for r in stored}
    for rid in _ORDER:
        merged[rid] = _RUNS[rid]
    # Same sort key as the repository's ORDER BY: ISO-8601 created_at descending,
    # run_id as the stable tiebreak.
    return sorted(
        merged.values(), key=lambda r: (r.created_at, r.run_id), reverse=True
    )


async def _load_history(store: Any) -> list[_Run]:
    """Stored runs, newest first. Empty when storage is disabled."""
    if store is None:
        return []
    return [_run_from_row(row) for row in await store.list_diagnostics_runs()]


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
    # Persisted per transition rather than once at the end: a run that outlives
    # its process (restart, OOM, deploy) leaves its last observed state on disk
    # instead of vanishing, and the history shows that it started.
    await _persist(store, run)
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
        # The terminal write. Reached on the success, timeout and failure paths
        # alike, so a stored run cannot be left at "running" by an outcome this
        # process actually observed.
        await _persist(store, run)


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

    # Trim the in-process working set: drop oldest terminal runs beyond the cap.
    # This no longer loses them -- they were persisted below and are served from
    # the table afterwards, which is why the visible history is not capped at 20.
    while len(_ORDER) > _HISTORY_CAP:
        candidate = _ORDER[0]
        if _RUNS[candidate].status in ("done", "failed"):
            _ORDER.pop(0)
            del _RUNS[candidate]
        else:
            break

    # Record the queued run BEFORE spawning the task. Awaiting after
    # create_task() would let _execute's "running" write land first and then be
    # overwritten by this stale "queued" one.
    await _persist(gateway.storage, run)

    task = asyncio.create_task(_execute(run, creds, gateway.storage))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)

    return {"run_id": run.run_id, "status": "queued"}


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    _auth: None = Depends(require_scope(ADMIN_SCOPE)),
    gateway: Gateway = Depends(get_gateway),
) -> dict[str, Any]:
    """Return a single run record by id, or 404.

    Memory first: a run this process is executing is mutating between polls, and
    the in-memory copy is always at least as fresh as the row. Storage answers
    for every other run, including one started before the last restart.
    """
    run = _RUNS.get(run_id) or await _load_run(gateway.storage, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    return _as_dict(run)


@router.get("/runs")
async def list_runs(
    _auth: None = Depends(require_scope(ADMIN_SCOPE)),
    gateway: Gateway = Depends(get_gateway),
) -> list[dict[str, Any]]:
    """Return the run history, newest first.

    Merged across storage and this process (see :func:`_history`). No longer
    capped at the process-local 20: ``_HISTORY_CAP`` bounds the working set, and
    the stored history ages out on the retention pass instead.
    """
    return [_as_dict(r) for r in await _history(gateway.storage)]
