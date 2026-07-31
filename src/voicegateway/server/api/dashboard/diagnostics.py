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

**The report export.** ``/runs/{id}/report`` (JSON, carrying
``schema_version``) and ``/runs/{id}/report.html`` (one self-contained file an
operator can hand to a client) are two renderings of ONE payload
(:func:`_report_payload`), so the file and the JSON cannot claim different
things. Neither decides anything: the verdict and the per-gate detail are read
back out of what :mod:`voicegateway.livekit_diag.gates` recorded on the run, and
a run recorded before gates existed says so rather than being re-judged at
export time against today's rules.

Every endpoint is gated behind require_scope(ADMIN_SCOPE): a run can place
billed calls and touch shared LiveKit infrastructure, so it needs the admin
role. That gate is a no-op when no API keys are configured (the local
single-operator default), and enforces the admin scope once auth is enabled.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from voicegateway._version import __version__
from voicegateway.core.auth import ADMIN_SCOPE
from voicegateway.livekit_diag import gates, service
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


async def _run_or_404(store: Any, run_id: str) -> _Run:
    """One run by id, from memory first and storage second, or 404.

    Memory first: a run this process is executing is mutating between polls, and
    the in-memory copy is always at least as fresh as the row. Storage answers
    for every other run, including one started before the last restart. Shared by
    every read endpoint so a run cannot be visible on one and missing on another.
    """
    run = _RUNS.get(run_id) or await _load_run(store, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    return run


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
    """Return a single run record by id, or 404 (see :func:`_run_or_404`)."""
    return _as_dict(await _run_or_404(gateway.storage, run_id))


@router.get("/runs/{run_id}/report")
async def get_run_report(
    run_id: str,
    _auth: None = Depends(require_scope(ADMIN_SCOPE)),
    gateway: Gateway = Depends(get_gateway),
) -> dict[str, Any]:
    """The machine-readable run report, versioned by ``schema_version``.

    Same payload the HTML file below is rendered from, so the two cannot say
    different things about the same run.
    """
    run = await _run_or_404(gateway.storage, run_id)
    return _report_payload(run)


@router.get("/runs/{run_id}/report.html", response_class=HTMLResponse)
async def get_run_report_html(
    run_id: str,
    _auth: None = Depends(require_scope(ADMIN_SCOPE)),
    gateway: Gateway = Depends(get_gateway),
) -> HTMLResponse:
    """The same report as ONE self-contained HTML file, served as a download.

    ``Content-Disposition: attachment`` on purpose. The document is inert (no
    script) and every interpolated value is escaped, but it carries strings this
    server does not own -- agent names, room names, provider error messages --
    and a downloaded file is never parsed in this origin's context.
    """
    run = await _run_or_404(gateway.storage, run_id)
    document = _render_report_html(_report_payload(run))
    return HTMLResponse(
        content=document,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{_report_filename(run.run_id)}"'
            )
        },
    )


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


# ---------------------------------------------------------------------------
# Run report: one payload, two renderings
# ---------------------------------------------------------------------------
#
# The report is a deliverable, not a debug dump: an operator attaches it to a
# ticket or hands it to a client, and it is read months later by somebody who was
# not in the room when it ran. That drives every decision below.
#
# * **One file, no network.** The HTML carries its own CSS inline and references
#   nothing external -- no CDN, no stylesheet, no font, no image, no script. It
#   renders identically from ``file://`` on a laptop with no internet.
# * **It carries its own context.** When the run happened, what it was pointed
#   at, the thresholds it was measured against, the tool version, and the schema
#   version are all in the document; a number alone is worthless out of band.
# * **It cannot look more complete than the run was.** A check that was not
#   requested, recorded nothing, or errored says exactly that where its numbers
#   would have been. Nothing unmeasured renders as 0, and nothing renders as
#   ``-`` where "measured as zero" and "never measured" differ.
# * **It does not decide anything.** The verdict and the per-gate reasoning are
#   read back out of what ``livekit_diag.gates`` recorded on the run. Re-judging
#   an old run at export time would produce a report that disagrees with the CI
#   log the same run printed.

#: Bumped only for a BREAKING change to the report payload.
#:
#: The contract for consumers, and the reason this field exists at all:
#:
#: * v1 is the first published shape.
#: * Within a major version the payload is ADDITIVE ONLY. New keys may appear;
#:   an existing key never changes meaning, type or nesting, and never
#:   disappears. A parser must therefore ignore keys it does not recognise.
#: * A value that was not measured is ``null`` (or the literal string
#:   ``"not_measured"`` for a field that is structurally always absent, e.g.
#:   packet loss). It is never 0, and a consumer must not coerce it to one.
#: * Anything that would break a v1 parser -- a removed key, a retyped value, a
#:   moved nesting level -- lands as ``schema_version: 2`` instead.
REPORT_SCHEMA_VERSION = 1

#: What ``schema_version`` versions. Lets a consumer that reads several
#: VoiceGateway exports tell them apart without guessing from shape.
REPORT_KIND = "voicegateway.diagnostics.run_report"

#: The machine token for a value nobody measured. The HTML spells it "not
#: measured". Both are the same claim, and neither is ever a zero.
NOT_MEASURED = "not_measured"

# Public tool version (the local-build "+g<sha>" suffix is noise in a
# deliverable, and it leaks a working-tree state to whoever receives the file).
_PUBLIC_VERSION = __version__.split("+", 1)[0]

# Verdicts are read, never derived, so this maps only what gates can produce.
# UNKNOWN is spelled out at length because it is the whole reason the gate work
# happened: a run that could not evaluate used to report a clean PASS, and a
# report that renders UNKNOWN as a tick would put that bug back into the one
# artifact the client actually reads.
_VERDICT_MEANING = {
    gates.PASS: (
        "Every gate this run evaluated was inside its threshold. It says nothing "
        "about anything the run did not measure."
    ),
    gates.WARN: (
        "Every gate evaluated, and at least one measured value is outside the "
        "threshold it was compared against."
    ),
    gates.UNKNOWN: (
        "At least one gate could NOT be evaluated. This is NOT a pass: the run "
        "did not demonstrate a healthy deployment, it failed to measure "
        "something. Read the gate rows below for which one, and treat the "
        "un-measured half as unknown rather than fine."
    ),
    gates.FAIL: (
        "At least one gate measured a failure, or a check did not complete."
    ),
}

# What this report structurally cannot tell you. Carried in the payload (not just
# the HTML) so an automated consumer inherits the caveats with the numbers.
_REPORT_LIMITS = [
    "Packet loss is not measured. The client SDK does not expose per-connection "
    "loss to the probe, so no loss figure and no loss series appears anywhere in "
    "this report. Connection quality is the coarse signal that is real.",
    "SFU round-trip time is the prober's own message through the SFU data "
    "channel and back. It is not the latency a caller hears an agent answer "
    "with; that is the reply-latency section.",
    "Reply latency is the slowest of at most three billed trial calls per agent. "
    f"Below {gates.MIN_PERCENTILE_SAMPLES} samples a percentile is not a "
    "percentile, so the tail is reported as \"max of N\" and never as a p95.",
    "LiveKit's server API reports a worker only once it has joined a room, so an "
    "idle registered agent is invisible to the agents check unless a heartbeat "
    "roster travelled with the run.",
    "Every number here comes from one vantage point: the host that ran the "
    "probe. It measures the path between that host and the deployment, not the "
    "path any particular caller takes.",
]


def _as_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _ms(seconds: Any) -> float | None:
    """Seconds -> milliseconds, keeping None as None (never 0.0)."""
    value = _as_float(seconds)
    return None if value is None else value * 1000.0


def _check_state(run: _Run, checks: dict[str, Any], name: str) -> dict[str, Any]:
    """How one check went, in the four states a report has to tell apart.

    ``not_requested`` (nobody asked for it), ``no_result`` (asked for, nothing
    recorded -- the run is still going or died), ``errored`` (it ran and broke)
    and ``ok``. Collapsing any two of these is how a report ends up looking more
    complete than the run was.
    """
    entry = checks.get(name)
    if not isinstance(entry, dict):
        state = "not_requested" if name not in run.checks else "no_result"
        return {"state": state, "error": None, "result": None}
    if not entry.get("ok"):
        return {
            "state": "errored",
            "error": str(entry.get("error") or "no reason was recorded"),
            "result": None,
        }
    result = entry.get("result")
    return {
        "state": "ok",
        "error": None,
        "result": result if isinstance(result, dict) else {},
    }


def _agents_finding(run: _Run, checks: dict[str, Any]) -> dict[str, Any]:
    """The two agent populations, kept apart.

    ``roster_configured`` is False when no collector was configured (nobody was
    asked) and True with an empty list when one was asked and reported nothing.
    Collapsing them would print a missing collector as an empty fleet.
    """
    got = _check_state(run, checks, "agents")
    out: dict[str, Any] = {
        "state": got["state"],
        "error": got["error"],
        "in_room": [],
        "in_room_count": None,
        "rooms": None,
        "roster_configured": None,
        "roster": [],
        "roster_counts": None,
    }
    result = got["result"]
    if result is None:
        return out
    rows = [r for r in (result.get("agents") or []) if isinstance(r, dict)]
    out["in_room"] = [
        {
            "agent_name": r.get("agent_name") or None,
            "room": r.get("room") or None,
            "state": r.get("state") or None,
            "humans": _as_int(r.get("humans")),
            # None when the server reported no join time: an unknown age, not a
            # brand new agent.
            "age_s": _as_float(r.get("age_s")),
        }
        for r in rows
    ]
    out["in_room_count"] = len(rows)
    out["rooms"] = len({r.get("room") for r in rows})
    roster = result.get("roster")
    if roster is None:
        out["roster_configured"] = False
        return out
    workers = [w for w in roster if isinstance(w, dict)]
    out["roster_configured"] = True
    out["roster"] = [
        {
            "agent_name": w.get("agent_name") or w.get("agent_id") or None,
            "status": w.get("status") or None,
            "region": w.get("region") or None,
            "version": w.get("version") or None,
        }
        for w in workers
    ]
    out["roster_counts"] = {
        status: sum(1 for w in workers if w.get("status") == status)
        for status in ("idle", "busy", "offline")
    }
    return out


def _sfu_finding(run: _Run, checks: dict[str, Any]) -> dict[str, Any]:
    """The idle SFU baseline, from whichever check measured one.

    ``sfu`` and ``sfu_load`` both produce a baseline; ``source`` records which
    one this came from so the reader is not left guessing. ``quality`` is None
    when the SDK reported ``Unknown`` -- that is "no client stayed connected long
    enough to read one", not a quality reading.
    """
    got = _check_state(run, checks, "sfu")
    source = "sfu"
    if got["state"] != "ok":
        alt = _check_state(run, checks, "sfu_load")
        if alt["state"] == "ok":
            got, source = alt, "sfu_load"
        elif got["state"] == "not_requested" and alt["state"] != "not_requested":
            got, source = alt, "sfu_load"
    result = got["result"] or {}
    baseline = result.get("baseline") if isinstance(result, dict) else None
    baseline = baseline if isinstance(baseline, dict) else {}
    quality = baseline.get("quality") or None
    return {
        "state": got["state"],
        "error": got["error"],
        "source": source if got["state"] != "not_requested" else None,
        "baseline_rtt_ms": _as_float(baseline.get("rtt_ms")),
        # "Unknown" is the probe's word for "no reading", so it is not passed on
        # as if it were one.
        "quality": None if quality == "Unknown" else quality,
        # Structural, not a value: see _REPORT_LIMITS.
        "packet_loss": NOT_MEASURED,
        "target_rtt_ms": _as_float(result.get("target_rtt_ms")),
    }


def _knee_finding(
    steps: list[dict[str, Any]], knee: int | None, target_rtt_ms: float | None
) -> dict[str, Any]:
    """Resolve ``knee`` against the ramp so a null knee is never ambiguous.

    ``find_knee`` returns None for two OPPOSITE outcomes: nothing breached the
    budget, or the FIRST tier already did. A report that prints "knee: none" for
    both labels a total failure as a clean ramp. Same classification the
    dashboard's Load tab does, on rtt against the budget, exactly as find_knee
    computed it (loss plays no part -- it is not measured).
    """
    if not steps:
        return {"kind": "no_ramp", "clients": None, "breach": None}
    if target_rtt_ms is None:
        # No threshold travelled with the ramp, so its steps cannot be compared
        # against anything and the ambiguity cannot be resolved. Saying so beats
        # inventing a budget.
        return {"kind": "no_budget", "clients": knee, "breach": None}
    breach = next(
        (
            s
            for s in steps
            if s["rtt_ms"] is not None and s["rtt_ms"] > target_rtt_ms
        ),
        None,
    )
    if knee is not None:
        return {"kind": "knee", "clients": knee, "breach": breach or steps[-1]}
    if breach is None:
        return {
            "kind": "all_healthy",
            "clients": max(
                (s["clients"] for s in steps if s["clients"] is not None), default=None
            ),
            "breach": None,
        }
    return {"kind": "first_tier_breached", "clients": None, "breach": breach}


def _load_finding(run: _Run, checks: dict[str, Any]) -> dict[str, Any]:
    """The client ramp, its knee, and the prober host's own load during it.

    The host block is not decoration: a laptop that pegged its own CPU at 25
    clients draws the same curve as an SFU that ran out of headroom, and this is
    the only thing that says which one happened. Its nulls mean "not sampled",
    never "idle", which is why ``saturated`` can be None.
    """
    got = _check_state(run, checks, "sfu_load")
    out: dict[str, Any] = {
        "state": got["state"],
        "error": got["error"],
        "ramp": [],
        "target_rtt_ms": None,
        "knee": None,
        "prober_host": None,
    }
    result = got["result"]
    if result is None:
        return out
    target = _as_float(result.get("target_rtt_ms"))
    steps: list[dict[str, Any]] = []
    for raw in result.get("ramp") or []:
        if not isinstance(raw, dict):
            continue
        rtt = _as_float(raw.get("rtt_ms"))
        quality = raw.get("quality") or None
        steps.append(
            {
                "clients": _as_int(raw.get("clients")),
                "rtt_ms": rtt,
                "quality": None if quality == "Unknown" else quality,
                # None, not False: with no budget there is nothing to be within.
                "within_budget": None if (target is None or rtt is None)
                else rtt <= target,
            }
        )
    out["ramp"] = steps
    out["target_rtt_ms"] = target
    out["knee"] = _knee_finding(steps, _as_int(result.get("knee")), target)
    resource = result.get("resource")
    if isinstance(resource, dict):
        per_client = resource.get("per_client") or {}
        saturated = resource.get("saturated")
        out["prober_host"] = {
            "saturated": None if saturated is None else bool(saturated),
            "cpu_peak_pct": _as_float(resource.get("cpu_peak")),
            "mem_peak_mb": _as_float(resource.get("mem_peak_mb")),
            "net_kbps_up": _as_float(resource.get("net_kbps_up")),
            "per_client_cpu_pct": _as_float(per_client.get("cpu_pct")),
            "per_client_kbps_up": _as_float(per_client.get("kbps_up")),
            "sustainable_clients": _as_int(resource.get("sustainable_n")),
        }
    return out


def _tail_finding(stats: dict[str, Any], trials: int) -> dict[str, Any] | None:
    """The slow tail, named for the statistic it actually is.

    ``summarize`` puts a ``p95`` on the wire computed from as few as one sample.
    A run places at most ``MAX_LATENCY_TRIALS`` = 3 billed calls per agent, so
    that number is the max wearing a more confident label, and this report never
    prints it as a percentile. The threshold is gates.MIN_PERCENTILE_SAMPLES, the
    same one the CLI gate names its metric after.
    """
    if trials >= gates.MIN_PERCENTILE_SAMPLES:
        p95 = _ms(stats.get("p95"))
        if p95 is not None:
            return {"statistic": "p95", "label": "p95", "value_ms": p95}
    value = _ms(stats.get("max"))
    if value is None:
        return None
    return {
        "statistic": f"max_of_{trials}",
        "label": f"max of {trials}",
        "value_ms": value,
    }


def _latency_finding(run: _Run, checks: dict[str, Any]) -> dict[str, Any]:
    """Reply latency per probed agent, in milliseconds.

    ``summarize`` returns 0.0 for every statistic when nothing answered, and a
    real reply cannot arrive in zero seconds, so ``trials == 0`` reports every
    number as null and the reader is told the probe measured nothing rather than
    being shown a suspiciously fast agent.
    """
    got = _check_state(run, checks, "latency")
    out: dict[str, Any] = {
        "state": got["state"],
        "error": got["error"],
        "target_ms": _as_float(run.config.get("target_ms")),
        "agents": [],
    }
    result = got["result"]
    if result is None:
        return out
    entries = []
    for raw in result.get("agents") or []:
        if not isinstance(raw, dict):
            continue
        raw_stats = raw.get("stats")
        stats: dict[str, Any] = raw_stats if isinstance(raw_stats, dict) else {}
        trials = _as_int(stats.get("trials")) or 0
        measured = trials > 0
        components = raw.get("components")
        split = None
        if isinstance(components, dict):
            split = {
                key: _ms(components.get(key))
                for key in ("eou", "stt", "stt_ttfp", "llm_ttft", "tts")
                if components.get(key) is not None
            }
        entries.append(
            {
                "agent": raw.get("agent") or None,
                "measured": measured,
                # Trials that ANSWERED. The payload does not carry the number
                # placed, so no denominator is implied.
                "trials_answered": trials,
                "avg_ms": _ms(stats.get("avg")) if measured else None,
                "min_ms": _ms(stats.get("min")) if measured else None,
                "tail": _tail_finding(stats, trials) if measured else None,
                # None means this host never saw the agent's own telemetry rows
                # (a remote collector, or an agent that is not instrumented), so
                # the per-leg split was never recorded here.
                "components_ms": split or None,
            }
        )
    out["agents"] = entries
    return out


def _report_target() -> dict[str, Any]:
    """What the report says it was pointed at, and how sure it is.

    The run row does NOT store the LiveKit server it probed, so this is resolved
    on the exporting host at export time. That is honest and useful in the common
    case (one deployment, unchanged config) and clearly labelled for the case
    where it is not, rather than silently attributing a run to a server that may
    have been reconfigured since.
    """
    try:
        url = _resolve_creds().url
    except CredsError:
        url = None
    return {
        "livekit_url": url,
        # False, always: no run record has ever carried its target.
        "recorded_with_run": False,
        "resolved": "on the exporting host, at export time",
    }


def _report_payload(run: _Run) -> dict[str, Any]:
    """The one report payload. The JSON export IS this; the HTML renders it.

    Nothing here judges the run. ``verdict`` and ``gates`` are read back out of
    what :mod:`voicegateway.livekit_diag.gates` recorded when the run executed;
    a run recorded before gates existed reports ``gates_recorded: false`` and
    keeps its stored verdict, instead of being re-judged against today's rules
    and disagreeing with the log it printed at the time.
    """
    results: dict[str, Any] = run.results if isinstance(run.results, dict) else {}
    raw_checks = results.get("checks")
    checks: dict[str, Any] = raw_checks if isinstance(raw_checks, dict) else {}
    stored_gates = results.get("gates")
    gates_recorded = isinstance(stored_gates, list)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "kind": REPORT_KIND,
        "generated_at": _now(),
        "generator": {"tool": "voicegateway", "version": _PUBLIC_VERSION},
        "run": {
            "run_id": run.run_id,
            "status": run.status,
            "checks_requested": list(run.checks),
            "config": dict(run.config),
            "created_at": run.created_at or None,
            "started_at": run.started_at,
            "ended_at": run.ended_at,
            "error": run.error,
        },
        "target": _report_target(),
        "verdict": {
            "status": run.verdict,
            "recorded": run.verdict is not None,
            "meaning": _VERDICT_MEANING.get(run.verdict or "") or None,
            "decided_by": "voicegateway.livekit_diag.gates",
        },
        "gates_recorded": gates_recorded,
        "gates": list(stored_gates) if isinstance(stored_gates, list) else None,
        "findings": {
            "agents": _agents_finding(run, checks),
            "sfu": _sfu_finding(run, checks),
            "load": _load_finding(run, checks),
            "latency": _latency_finding(run, checks),
        },
        "not_measured": list(_REPORT_LIMITS),
    }


def _report_filename(run_id: str) -> str:
    """A download filename that cannot smuggle anything into the header.

    ``run_id`` reaches here from the URL path, so it is stripped to the
    characters a filename needs before it is interpolated into
    Content-Disposition.
    """
    safe = re.sub(r"[^A-Za-z0-9_-]", "", run_id)[:32] or "run"
    return f"voicegateway-diagnostics-{safe}.html"


# ---------------------------------------------------------------------------
# HTML rendering: one file, no network, readable months later
# ---------------------------------------------------------------------------

# Inline, and deliberately boring. No @font-face, no url(), no import: a font or
# an image the browser has to fetch is exactly what stops this file rendering
# from a laptop with no internet. The status colours are decoration only -- every
# status is also spelled out in words, so the report survives being printed in
# black and white or read by somebody who cannot distinguish them.
_REPORT_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 32px 28px 64px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica,
    Arial, sans-serif;
  font-size: 15px; line-height: 1.55; color: #16181d; background: #fff;
  max-width: 980px;
}
h1 { font-size: 22px; margin: 0 0 4px; letter-spacing: -0.01em; }
h2 { font-size: 16px; margin: 34px 0 10px; padding-bottom: 6px;
     border-bottom: 1px solid #e3e6ea; }
h3 { font-size: 14px; margin: 20px 0 6px; }
p { margin: 8px 0; }
.sub { color: #5b6472; font-size: 13px; margin: 0 0 18px; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.meta { border: 1px solid #e3e6ea; border-radius: 6px; padding: 14px 16px;
        margin: 18px 0; }
.meta dl { display: grid; grid-template-columns: 220px 1fr; gap: 6px 18px;
           margin: 0; font-size: 13px; }
.meta dt { color: #5b6472; }
.meta dd { margin: 0; }
.verdict { border: 2px solid #16181d; border-radius: 6px; padding: 16px 18px;
           margin: 18px 0 6px; }
.verdict .word { font-size: 26px; font-weight: 800; letter-spacing: 0.02em; }
.verdict.pass { border-color: #1f7a44; background: #f2faf5; }
.verdict.warn { border-color: #a86a00; background: #fdf7ec; }
.verdict.unknown { border-color: #4a4f5a; background: #f4f5f7; }
.verdict.fail { border-color: #a32020; background: #fdf3f3; }
table { border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 13px; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid #e9ebef;
         vertical-align: top; }
th { background: #f6f7f9; font-weight: 600; }
td.num, th.num { text-align: right;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.tag { display: inline-block; padding: 1px 7px; border-radius: 3px;
       font-size: 11px; font-weight: 700; letter-spacing: 0.03em;
       border: 1px solid #c9ced6; }
.tag.pass { border-color: #1f7a44; color: #1f7a44; }
.tag.warn { border-color: #a86a00; color: #a86a00; }
.tag.unknown { border-color: #4a4f5a; color: #4a4f5a; }
.tag.fail { border-color: #a32020; color: #a32020; }
.nm { color: #6b7280; font-style: italic; }
.note { border-left: 3px solid #c9ced6; padding: 8px 12px; margin: 10px 0;
        background: #f8f9fb; font-size: 13px; color: #3d434d; }
.note.bad { border-left-color: #a32020; background: #fdf3f3; }
.note.warn { border-left-color: #a86a00; background: #fdf7ec; }
.note.good { border-left-color: #1f7a44; background: #f2faf5; }
ul { margin: 8px 0; padding-left: 20px; font-size: 13px; color: #3d434d; }
li { margin: 4px 0; }
footer { margin-top: 40px; padding-top: 12px; border-top: 1px solid #e3e6ea;
         font-size: 12px; color: #5b6472; }
@media print { body { padding: 0; max-width: none; } h2 { break-after: avoid; } }
"""

_STATE_TEXT = {
    "not_requested": (
        "This check was not part of the run, so nothing in this section was "
        "measured. It is absent, not clean."
    ),
    "no_result": (
        "This check was requested but recorded no result: the run did not get "
        "far enough to report one."
    ),
}


def _esc(value: Any) -> str:
    """Escape anything that goes into the document. Nothing is trusted."""
    return html.escape("" if value is None else str(value), quote=True)


def _num(value: float | None, unit: str, digits: int = 0) -> str:
    """A measured number, or the "not measured" marker. Never a fabricated 0."""
    if value is None:
        return '<span class="nm">not measured</span>'
    rendered = f"{value:,.{digits}f}"
    return f"{rendered}&nbsp;{_esc(unit)}" if unit else rendered


def _plain(value: Any) -> str:
    """A measured string, or the "not measured" marker."""
    if value is None or value == "":
        return '<span class="nm">not measured</span>'
    return _esc(value)


def _recorded(value: Any, suffix: str = "") -> str:
    """A run PARAMETER, or the "not recorded" marker.

    Distinct from :func:`_plain` on purpose: a threshold the run was configured
    with is not a measurement, and calling its absence "not measured" would
    misfile a missing setting as a failed reading.
    """
    if value is None or value == "":
        return '<span class="nm">not recorded</span>'
    return _esc(value) + suffix


def _state_note(finding: dict[str, Any]) -> str | None:
    """The banner for a check that produced nothing, or None when it produced."""
    state = finding.get("state")
    if state == "errored":
        return (
            '<div class="note bad">This check did not complete: '
            f"{_esc(finding.get('error'))}. Nothing below it was measured.</div>"
        )
    text = _STATE_TEXT.get(str(state))
    if text is not None:
        return f'<div class="note">{_esc(text)}</div>'
    return None


def _render_meta(payload: dict[str, Any]) -> str:
    run = payload["run"]
    target = payload["target"]
    config = run.get("config") or {}
    target_ms = _as_float(config.get("target_ms"))
    url = target.get("livekit_url")
    url_cell = (
        f'<span class="mono">{_esc(url)}</span>'
        if url
        else '<span class="nm">not recorded: no LiveKit credentials are '
        "configured on the host that exported this report</span>"
    )
    rows = [
        ("Run id", f'<span class="mono">{_esc(run["run_id"])}</span>'),
        ("Run status", _plain(run.get("status"))),
        ("Queued", _plain(run.get("created_at"))),
        ("Started", _plain(run.get("started_at"))),
        ("Ended", _plain(run.get("ended_at"))),
        ("Checks requested", _esc(", ".join(run.get("checks_requested") or [])) or
         '<span class="nm">none recorded</span>'),
        (
            "LiveKit server",
            url_cell
            + '<br><span class="nm">read on the exporting host at export time: '
            "the run record does not store the server it probed</span>",
        ),
        (
            "Reply-latency target",
            _recorded(None if target_ms is None else f"{target_ms:,.0f} ms"),
        ),
        (
            "Trials per agent",
            _recorded(config.get("trials"), " (each one a real, billed call)"),
        ),
        (
            "Ramp tiers",
            _recorded(", ".join(str(n) for n in (config.get("ramp") or []))),
        ),
        ("Report generated", _esc(payload["generated_at"])),
        (
            "Generated by",
            f'voicegateway {_esc(payload["generator"]["version"])}'
            f' &middot; report schema v{payload["schema_version"]}',
        ),
    ]
    body = "".join(f"<dt>{label}</dt><dd>{value}</dd>" for label, value in rows)
    return f'<div class="meta"><dl>{body}</dl></div>'


def _render_verdict(payload: dict[str, Any]) -> str:
    verdict = payload["verdict"]
    status = verdict.get("status")
    run = payload["run"]
    if status is None:
        detail = run.get("error") or "the reason was not recorded"
        return (
            '<div class="verdict unknown"><div class="word">NO VERDICT</div>'
            "<p>This run recorded no verdict, so it demonstrates nothing either "
            f"way: {_esc(detail)}.</p></div>"
        )
    css = str(status).lower()
    css = css if css in ("pass", "warn", "unknown", "fail") else "unknown"
    meaning = verdict.get("meaning") or (
        "This status was recorded by the run and is reproduced verbatim; this "
        "build does not know how to explain it."
    )
    return (
        f'<div class="verdict {css}"><div class="word">{_esc(status)}</div>'
        f"<p>{_esc(meaning)}</p>"
        "<p class=\"nm\">The verdict is the worst gate below "
        "(PASS &lt; WARN &lt; UNKNOWN &lt; FAIL), decided when the run "
        "executed, not when this report was exported.</p></div>"
    )


def _render_gates(payload: dict[str, Any]) -> str:
    if not payload.get("gates_recorded"):
        return (
            '<h2>Gates</h2><div class="note">This run recorded no per-gate '
            "detail. It predates per-gate provenance in this deployment, so only "
            "the overall verdict above survives from it. It has deliberately NOT "
            "been re-judged at export time: that would produce a report "
            "disagreeing with what the run itself reported.</div>"
        )
    stored = payload["gates"] or []
    rows = []
    for gate in stored:
        if not isinstance(gate, dict):
            continue
        status = str(gate.get("status") or "UNKNOWN")
        css = status.lower() if status.lower() in (
            "pass", "warn", "unknown", "fail"
        ) else "unknown"
        subject = gate.get("subject")
        value = _as_float(gate.get("value"))
        threshold = _as_float(gate.get("threshold"))
        rows.append(
            f'<tr><td><span class="tag {css}">{_esc(status)}</span></td>'
            f'<td class="mono">{_esc(gate.get("gate"))}'
            + (f"<br>{_esc(subject)}" if subject else "")
            + f"</td><td>{_esc(gate.get('detail'))}</td>"
            # A gate can legitimately carry no metric: agents_listing asserts the
            # server answered, and an UNKNOWN gate never got as far as a number.
            # "not recorded" says that; calling a missing NAME "not measured"
            # would file it as a failed reading.
            f'<td class="mono">{_recorded(gate.get("metric"))}</td>'
            f'<td class="num">{_num(value, "", 1)}</td>'
            f'<td class="num">{_num(threshold, "", 1)}</td></tr>'
        )
    if not rows:
        if stored:
            return (
                "<h2>Gates</h2>"
                f'<div class="note">This run recorded {len(stored)} gate '
                "entries, and none of them is in a shape this build can read. "
                "They are not reproduced rather than guessed at; read the run "
                "payload directly.</div>"
            )
        return (
            "<h2>Gates</h2>"
            '<div class="note">This run evaluated no gate at all: no check '
            "produced anything a gate knows how to read.</div>"
        )
    return (
        "<h2>Gates</h2><table><thead><tr><th>Status</th><th>Gate</th>"
        "<th>What it found</th><th>Metric</th><th class=\"num\">Value</th>"
        "<th class=\"num\">Threshold</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
        '<div class="note">A gate reading UNKNOWN did not evaluate. It is not a '
        "pass with a caveat: nothing was compared against anything, and the "
        "value it would have reported is unknown.</div>"
    )


def _render_agents(finding: dict[str, Any]) -> str:
    out = ["<h2>Agents</h2>"]
    note = _state_note(finding)
    if note is not None:
        return "".join(out) + note
    rows = finding.get("in_room") or []
    out.append(
        f"<p>{finding.get('in_room_count')} agent(s) in "
        f"{finding.get('rooms')} room(s) at the moment this check ran.</p>"
    )
    if rows:
        body = "".join(
            "<tr>"
            f"<td>{_plain(r.get('agent_name'))}</td>"
            f'<td class="mono">{_plain(r.get("room"))}</td>'
            f"<td>{_plain(r.get('state'))}</td>"
            f'<td class="num">{_plain(r.get("humans"))}</td>'
            f'<td class="num">{_num(r.get("age_s"), "s")}</td></tr>'
            for r in rows
        )
        out.append(
            "<table><thead><tr><th>Agent</th><th>Room</th><th>State</th>"
            '<th class="num">Humans</th><th class="num">Age</th></tr></thead>'
            f"<tbody>{body}</tbody></table>"
        )
    else:
        out.append(
            '<div class="note">No agent was in a room when this check ran. '
            "LiveKit reports a worker only once it has JOINED a room, so an idle "
            "worker that is running perfectly looks identical to no worker at "
            "all here. The heartbeat roster is what tells them apart.</div>"
        )
    configured = finding.get("roster_configured")
    if configured is False:
        out.append(
            '<h3>Registered workers (heartbeat roster)</h3><div class="note">'
            "No collector was configured for this run, so the idle/registered "
            "fleet was never asked and is not measured here. That is different "
            "from a fleet of zero workers.</div>"
        )
    elif configured:
        workers = finding.get("roster") or []
        counts = finding.get("roster_counts") or {}
        out.append(
            f"<h3>Registered workers (heartbeat roster)</h3><p>{len(workers)} "
            f"registered &middot; {counts.get('idle', 0)} idle &middot; "
            f"{counts.get('busy', 0)} busy &middot; "
            f"{counts.get('offline', 0)} offline.</p>"
        )
        if workers:
            body = "".join(
                f"<tr><td>{_plain(w.get('agent_name'))}</td>"
                f"<td>{_plain(w.get('status'))}</td>"
                f"<td>{_plain(w.get('region'))}</td>"
                f'<td class="mono">{_plain(w.get("version"))}</td></tr>'
                for w in workers
            )
            out.append(
                "<table><thead><tr><th>Worker</th><th>Status</th><th>Region</th>"
                f"<th>Version</th></tr></thead><tbody>{body}</tbody></table>"
            )
        else:
            out.append(
                '<div class="note">The collector was configured but reported no '
                "worker: either none has sent a heartbeat yet, or it could not "
                "be reached on this run.</div>"
            )
    return "".join(out)


def _render_sfu(finding: dict[str, Any]) -> str:
    out = ["<h2>SFU baseline</h2>"]
    note = _state_note(finding)
    if note is not None:
        return "".join(out) + note
    source = finding.get("source")
    measured_by = (
        f' (measured by the <span class="mono">{_esc(source)}</span> check)'
        if source
        else ""
    )
    out.append(
        f"<p>Two synthetic clients, idle, talking through the SFU"
        f"{measured_by}.</p>"
    )
    out.append(
        "<table><tbody>"
        "<tr><td>Round trip through the SFU (data channel)</td>"
        f'<td class="num">{_num(finding.get("baseline_rtt_ms"), "ms", 1)}</td></tr>'
        "<tr><td>Connection quality (SDK)</td>"
        f"<td class=\"num\">{_plain(finding.get('quality'))}</td></tr>"
        "<tr><td>Packet loss</td>"
        '<td class="num"><span class="nm">not measured</span></td></tr>'
        "<tr><td>Budget the ramp was compared against</td>"
        f'<td class="num">{_num(finding.get("target_rtt_ms"), "ms")}</td></tr>'
        "</tbody></table>"
    )
    out.append(
        '<div class="note">Packet loss is not measured and no loss figure is '
        "reported anywhere in this document: the probe reads a hardcoded "
        "placeholder, not the connection. Quality is the coarse signal that is "
        "real, and a quality of &ldquo;not measured&rdquo; means no client "
        "stayed connected long enough to read one.</div>"
    )
    return "".join(out)


def _budget_cell(within: Any) -> str:
    """A ramp tier against the budget. None is a third answer, not a failure.

    None means no rtt budget travelled with the ramp, so the tier was never
    compared to anything: rendering it as "over" or "within" would report a
    comparison that did not happen.
    """
    if within is True:
        return "within budget"
    if within is False:
        return "over budget"
    return '<span class="nm">no budget to compare against</span>'


def _render_knee(knee: dict[str, Any], target: float | None) -> str:
    kind = knee.get("kind")
    breach = knee.get("breach") or {}
    budget = _num(target, "ms")
    if kind == "no_ramp":
        return (
            '<div class="note">This run measured no ramp, so there is no '
            "capacity curve and no knee. Nothing is inferred from an absent "
            "measurement.</div>"
        )
    if kind == "no_budget":
        return (
            '<div class="note">No rtt budget travelled with this ramp, so its '
            "tiers cannot be compared against anything and the knee cannot be "
            "resolved. A knee of &ldquo;none&rdquo; means two opposite things "
            "(nothing broke, or the first tier broke), and without the budget "
            "this report cannot say which.</div>"
        )
    if kind == "knee":
        return (
            f'<div class="note warn">Knee at {_esc(knee.get("clients"))} clients: '
            f"every tier up to {_esc(knee.get('clients'))} stayed inside the "
            f"{budget} budget, and {_esc(breach.get('clients'))} clients broke it "
            f"at {_num(breach.get('rtt_ms'), 'ms', 1)}.</div>"
        )
    if kind == "all_healthy":
        return (
            '<div class="note good">No knee inside this ramp: every tier up to '
            f"{_esc(knee.get('clients'))} clients stayed under the {budget} "
            "budget. That is a floor on capacity, not a ceiling: the ramp "
            "stopped there, it did not find a limit.</div>"
        )
    return (
        '<div class="note bad">No healthy tier in this ramp: the smallest tier '
        f"({_esc(breach.get('clients'))} clients) already exceeded the {budget} "
        f"budget at {_num(breach.get('rtt_ms'), 'ms', 1)}. There is no knee "
        "because nothing passed, which is the opposite of a clean ramp.</div>"
    )


def _render_load(finding: dict[str, Any]) -> str:
    out = ["<h2>SFU load ramp</h2>"]
    note = _state_note(finding)
    if note is not None:
        return "".join(out) + note
    steps = finding.get("ramp") or []
    knee = finding.get("knee") or {"kind": "no_ramp"}
    out.append(_render_knee(knee, finding.get("target_rtt_ms")))
    if steps:
        body = "".join(
            f'<tr><td class="num">{_plain(s.get("clients"))}</td>'
            f'<td class="num">{_num(s.get("rtt_ms"), "ms", 1)}</td>'
            f"<td>{_plain(s.get('quality'))}</td>"
            f"<td>{_budget_cell(s.get('within_budget'))}</td></tr>"
            for s in steps
        )
        out.append(
            '<table><thead><tr><th class="num">Clients</th>'
            '<th class="num">Round trip</th><th>Quality</th>'
            f"<th>Against budget</th></tr></thead><tbody>{body}</tbody></table>"
        )
        out.append(
            '<div class="note">Round trip and quality are the only measured '
            "columns, and that round trip is the prober&rsquo;s own message "
            "through the SFU data channel and back, not the latency a caller "
            "hears an agent answer with.</div>"
        )
    host = finding.get("prober_host")
    out.append("<h3>Prober host during the ramp</h3>")
    if host is None:
        out.append(
            '<div class="note">The prober&rsquo;s own CPU, memory and uplink '
            "were not sampled on this run, so its numbers cannot be attributed "
            "to the SFU rather than to this machine.</div>"
        )
        return "".join(out)
    saturated = host.get("saturated")
    if saturated is True:
        out.append(
            '<div class="note bad">This host saturated during the ramp. The '
            "curve above describes THIS machine, not the SFU: re-run from a "
            "larger host, or with lower tiers, before reading the knee as a "
            "server limit.</div>"
        )
    elif saturated is False:
        out.append(
            '<div class="note good">This host did not saturate, so the ramp '
            "above is a measurement of the SFU rather than of the prober.</div>"
        )
    else:
        out.append(
            '<div class="note warn">Saturation is unknown for this run: no '
            "usable CPU sample was taken, so nobody can say whether the prober "
            "or the SFU was the limit. It is not reported as &ldquo;not "
            "saturated&rdquo;, because that was never measured.</div>"
        )
    out.append(
        "<table><tbody>"
        f'<tr><td>Peak CPU on the prober</td><td class="num">'
        f'{_num(host.get("cpu_peak_pct"), "%", 1)}</td></tr>'
        f'<tr><td>Peak memory (RSS)</td><td class="num">'
        f'{_num(host.get("mem_peak_mb"), "MB")}</td></tr>'
        f'<tr><td>Uplink during the ramp</td><td class="num">'
        f'{_num(host.get("net_kbps_up"), "kbps")}</td></tr>'
        f'<tr><td>CPU per client</td><td class="num">'
        f'{_num(host.get("per_client_cpu_pct"), "%", 2)}</td></tr>'
        f'<tr><td>Uplink per client</td><td class="num">'
        f'{_num(host.get("per_client_kbps_up"), "kbps", 1)}</td></tr>'
        f'<tr><td>Clients this host sustains</td><td class="num">'
        f'{_plain(host.get("sustainable_clients"))}</td></tr>'
        "</tbody></table>"
    )
    return "".join(out)


def _render_latency(finding: dict[str, Any]) -> str:
    out = ["<h2>Reply latency (real calls)</h2>"]
    note = _state_note(finding)
    if note is not None:
        return "".join(out) + note
    agents = finding.get("agents") or []
    if not agents:
        out.append(
            '<div class="note">No agent was in a room to call, so no reply '
            "latency was measured. The run probes the agents LiveKit reported; "
            "it never invents a target to dial.</div>"
        )
        return "".join(out)
    target = finding.get("target_ms")
    for agent in agents:
        out.append(f"<h3>{_plain(agent.get('agent'))}</h3>")
        if not agent.get("measured"):
            out.append(
                '<div class="note bad">Nothing was measured for this agent: no '
                "trial produced a reply. The end-to-end time is reported as not "
                "measured rather than as a zero, which would read as an instant "
                "answer.</div>"
            )
            continue
        tail = agent.get("tail") or {}
        split = agent.get("components_ms")
        rows = [
            (
                f"Slowest reply ({_esc(tail.get('label') or 'tail')}, end to end)",
                _num(tail.get("value_ms"), "ms"),
            ),
            ("Average reply", _num(agent.get("avg_ms"), "ms")),
            ("Fastest reply", _num(agent.get("min_ms"), "ms")),
            ("Trials that answered", _plain(agent.get("trials_answered"))),
            ("Target this run was measured against", _num(target, "ms")),
        ]
        if split:
            labels = {
                "eou": "Turn detection (end of utterance)",
                "stt": "STT",
                "stt_ttfp": "STT time to first partial",
                "llm_ttft": "LLM time to first token",
                "tts": "TTS",
            }
            rows.extend(
                (labels.get(key, key), _num(value, "ms"))
                for key, value in split.items()
            )
        body = "".join(
            f'<tr><td>{label}</td><td class="num">{value}</td></tr>'
            for label, value in rows
        )
        out.append(f"<table><tbody>{body}</tbody></table>")
        if not split:
            out.append(
                '<div class="note">The STT/LLM/TTS split was not recorded for '
                "this agent: the split is read back out of the rows the agent "
                "itself writes, and this host never saw any for the probe room "
                "(an uninstrumented agent, or one reporting to a remote "
                "collector).</div>"
            )
        trials = agent.get("trials_answered") or 0
        if tail.get("statistic", "").startswith("max_of"):
            out.append(
                f'<div class="note">{trials} sample(s) is below the '
                f"{gates.MIN_PERCENTILE_SAMPLES} a percentile needs, so the "
                f"headline is the max of {trials} and not a p95. A run places at "
                "most three calls per agent, because each one is billed.</div>"
            )
    return "".join(out)


def _render_limits(payload: dict[str, Any]) -> str:
    items = "".join(f"<li>{_esc(item)}</li>" for item in payload["not_measured"])
    return (
        "<h2>What this report does not measure</h2>"
        "<p>Read this section before acting on anything above. A diagnostics run "
        "is a single-vantage snapshot, and these are its structural limits, not "
        "this run&rsquo;s bad luck.</p>"
        f"<ul>{items}</ul>"
    )


def _render_report_html(payload: dict[str, Any]) -> str:
    """Render ``payload`` as ONE self-contained HTML document.

    Self-contained is a hard requirement, not a nicety: this file is handed to
    somebody outside the deployment and opened from disk, possibly offline,
    possibly years later. There is no <script>, no <link>, no <img>, no external
    font and no url() anywhere in it -- the CSS is inline and the only content is
    text. ``test_diagnostics_report.py`` asserts each of those, because it is the
    kind of property a later edit breaks silently by pasting in a chart library.
    """
    findings = payload["findings"]
    run_id = _esc(payload["run"]["run_id"])
    body = "".join(
        [
            "<h1>LiveKit diagnostics run report</h1>",
            f'<p class="sub">VoiceGateway &middot; run '
            f'<span class="mono">{run_id}</span></p>',
            _render_verdict(payload),
            _render_meta(payload),
            _render_gates(payload),
            _render_agents(findings["agents"]),
            _render_sfu(findings["sfu"]),
            _render_load(findings["load"]),
            _render_latency(findings["latency"]),
            _render_limits(payload),
            "<footer>Generated by voicegateway "
            f'{_esc(payload["generator"]["version"])} on '
            f'{_esc(payload["generated_at"])} &middot; report schema v'
            f'{payload["schema_version"]} ({_esc(payload["kind"])}). This file is '
            "self-contained: it loads nothing over the network and reads the same "
            "offline.</footer>",
        ]
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>LiveKit diagnostics report {run_id}</title>"
        f"<style>{_REPORT_CSS}</style></head><body>{body}</body></html>\n"
    )
