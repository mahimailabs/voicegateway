"""Dashboard endpoints: GET /api/agents, /api/agents/{agent_id}, POST .../probe.

Phase 2 fleet dashboard. Agents are derived from DISTINCT requests.agent_id;
this index feeds the Agents page (fleet table) and the agent-filter typeahead.
Per-agent p95 latency is merged in from a single windowed query so the index
stays O(1) queries.

The probe endpoint places one real call to a single agent and reports what that
call actually cost and how long each leg of its cascade took. It is admin-scoped
and rate limited because every press is billed traffic against real providers.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
import uuid
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from voicegateway.core.auth import ADMIN_SCOPE
from voicegateway.livekit_diag import service as diag_service
from voicegateway.livekit_diag.config import CredsError, resolve_creds
from voicegateway.repository import agent_observations_repository as agent_obs
from voicegateway.repository import agents_repository as agents
from voicegateway.repository import request_log_repository, workers_repository
from voicegateway.repository.workers_repository import DEFAULT_TTL_SECONDS
from voicegateway.server.api._deps import get_gateway, require_scope

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway
    from voicegateway.repository.agent_observations_repository import (
        AgentObservationRow,
    )
    from voicegateway.repository.workers_repository import RosterRow

router = APIRouter(prefix="/agents", tags=["dashboard"])

_EMPTY_UNATTRIBUTED = {
    "request_count": 0,
    "total_cost_usd": 0.0,
    "last_seen": None,
    "error_rate": 0.0,
}


def _error_rate(error_count: int, request_count: int) -> float:
    """Derive error_rate from rollup counts (a stored row has request_count >=
    1, so the divide is safe; guard a zero denominator defensively)."""
    return (error_count / request_count) if request_count else 0.0


def _memory_pct(rss: int | None, total: int | None) -> float | None:
    """RSS as a percentage of the memory ceiling (None when unavailable)."""
    if not rss or not total:
        return None
    return round(rss / total * 100, 1)


def _latency_stack(latency: dict[str, float]) -> dict[str, float | None]:
    """Per-modality average first-byte latency for the card waterfall.

    Only STT / LLM / TTS are measured (first-byte per modality); the network
    hops and turn-detection segments in a colocation diagram are not metered, so
    the waterfall is honest about the three segments it can show.
    """
    return {
        "stt": latency.get("stt"),
        "llm": latency.get("llm"),
        "tts": latency.get("tts"),
    }


# A probe places a real call through the agent's real cascade, so it is billed
# like any other call. One in flight per agent, and no more often than this.
# Both limits are per agent on purpose: probing agent A must never block or
# throttle a press on agent B.
PROBE_COOLDOWN_SECONDS = 30.0

_PROBES_INFLIGHT: set[str] = set()
_PROBE_LAST_RUN: dict[str, float] = {}


def _resolve_creds() -> Any:
    """Seam for tests; reads LiveKit creds from env / voicegw.yaml, never stored."""
    return resolve_creds(None, None, None)


def _livekit_configured() -> bool:
    try:
        _resolve_creds()
    except CredsError:
        return False
    return True


def _probe_block(
    agent_id: str | None,
    dispatch_names: dict[str, str],
    roster_names: dict[str, str],
    *,
    livekit_configured: bool,
    automatic_count: int,
) -> dict[str, Any]:
    """Whether this agent can be probed, and if not, why not.

    The dispatch name is LiveKit's ``agent_name``: the value on
    ``@server.rtc_session(agent_name=...)`` (or the legacy ``WorkerOptions``)
    that explicit dispatch routes a job by. VoiceGateway learns it from two
    places, most-trusted first:

    - OBSERVED: ``Job.agent_name`` as ``attach`` read it back off a call the
      agent actually ran. Proven, because a real job carried it.
    - ROSTER: the ``agent_name`` the worker passed to ``register_worker`` when
      it came online and started heartbeating. It is the same value a worker
      registers with LiveKit under, available the instant the agent connects,
      before it has served a single call. Used only when nothing was observed.

    A roster name is what the worker *claimed*, not what a finished job proved,
    so it can be wrong if someone registers under one name and dispatches under
    another. That does not make the probe fake: dispatching to a wrong name
    reaches no worker, and the probe reports that as a failure (the runner
    fails fast once no worker joins) rather than inventing a number. The reason
    string says the name is unverified so the operator reads it as such.

    The resolved name maps to a mode:

    - a non-empty name: explicit dispatch, targeted at exactly this agent.
    - ``""``: the worker registered no agent_name, so LiveKit has it on
      automatic dispatch and it joins every new room. Creating the room is the
      whole dispatch. With more than one such worker known, whichever is online
      grabs the job, so the reason says so instead of implying precision.
    - absent from BOTH sources: never observed and not in the live roster (no
      instrumented call yet and no heartbeat, or the agent ships its telemetry
      to a remote collector and never registered here).
    """
    if not agent_id:
        return {
            "eligible": False,
            "dispatch_name": None,
            "mode": None,
            "reason": "unattributed traffic has no agent to dispatch to",
        }
    if not livekit_configured:
        return {
            "eligible": False,
            "dispatch_name": None,
            "mode": None,
            "reason": (
                "LiveKit is not configured on this host: set LIVEKIT_URL, "
                "LIVEKIT_API_KEY and LIVEKIT_API_SECRET"
            ),
        }
    observed = dispatch_names.get(agent_id)
    name = observed if observed is not None else roster_names.get(agent_id)
    verified = observed is not None
    if name is None:
        return {
            "eligible": False,
            "dispatch_name": None,
            "mode": None,
            "reason": (
                "no LiveKit job observed for this agent yet and no live worker "
                "in the roster: VoiceGateway dispatches by the agent_name a "
                "worker registers with, and has seen neither"
            ),
        }
    if name:
        return {
            "eligible": True,
            "dispatch_name": name,
            "mode": "explicit",
            "reason": (
                None
                if verified
                else (
                    f"dispatch name {name!r} is how this worker registered; the "
                    "probe has not yet confirmed it against a completed call"
                )
            ),
        }
    return {
        "eligible": True,
        "dispatch_name": "",
        "mode": "automatic",
        "reason": (
            (
                f"this worker uses automatic dispatch, and {automatic_count} "
                "agents here have registered that way: whichever of them is "
                "online may answer the probe"
            )
            if automatic_count > 1
            else None
        ),
    }


def _agent_entry(
    row: AgentObservationRow,
    memory_pct: float | None,
    models: dict[str, str],
    latency: dict[str, float],
    *,
    fleet_status: str | None = None,
    last_seen: float | None = None,
    agent_name: str | None = None,
    probe: dict[str, Any],
) -> dict[str, Any]:
    return {
        "agent_id": row.agent_id,
        # Friendly label from the worker roster (matches Server > Fleet); None for
        # a telemetry-only agent, where the UI falls back to agent_id.
        "agent_name": agent_name,
        "request_count": row.request_count,
        "total_cost_usd": row.total_cost_usd,
        # A registered agent's heartbeat is fresher than its last request, so the
        # merged last_seen keeps a live-but-idle agent from reading as dormant.
        "last_seen": last_seen if last_seen is not None else row.last_seen,
        "error_rate": _error_rate(row.error_count, row.request_count),
        "p95_latency_ms": row.p95_ms,
        "memory_pct": memory_pct,
        "models": {
            "stt": models.get("stt"),
            "llm": models.get("llm"),
            "tts": models.get("tts"),
        },
        # Average STT/LLM/TTS first-byte latency (24h) for the card waterfall.
        "latency_ms": _latency_stack(latency),
        # idle/busy/offline from the worker roster; None when the agent is not
        # currently registered (telemetry-only, e.g. a past run).
        "fleet_status": fleet_status,
        # Whether the card's play button can place a probe, and why not if it
        # cannot. See _probe_block.
        "probe": probe,
    }


def _roster_only_entry(
    w: RosterRow, probe: dict[str, Any], models: dict[str, str]
) -> dict[str, Any]:
    """A registered worker that has not written any rollup telemetry yet.

    Lets a booted-but-idle agent show on the Agents page (matching Server > Fleet)
    instead of appearing only once it has handled a call. ``models`` is its last-
    seen STT/LLM/TTS stack (from any traffic, including a probe): 0 rollup requests
    does not mean 0 knowledge of which models it runs.
    """
    return {
        "agent_id": w.agent_id,
        "agent_name": w.agent_name,
        "request_count": 0,
        "total_cost_usd": 0.0,
        "last_seen": w.last_seen,
        "error_rate": 0.0,
        "p95_latency_ms": None,
        "memory_pct": _memory_pct(w.memory_rss_bytes, w.memory_total_bytes),
        "models": {
            "stt": models.get("stt"),
            "llm": models.get("llm"),
            "tts": models.get("tts"),
        },
        # A booted-but-idle worker has metered nothing yet, so no latency stack.
        "latency_ms": {"stt": None, "llm": None, "tts": None},
        "fleet_status": w.status,
        "probe": probe,
    }


def _merged_last_seen(rollup: float | None, roster: float | None) -> float | None:
    vals = [v for v in (rollup, roster) if v is not None]
    return max(vals) if vals else None


def _unattributed_entry(row: AgentObservationRow | None) -> dict[str, Any]:
    if row is None:
        return dict(_EMPTY_UNATTRIBUTED)
    return {
        "request_count": row.request_count,
        "total_cost_usd": row.total_cost_usd,
        "last_seen": row.last_seen,
        "error_rate": _error_rate(row.error_count, row.request_count),
    }


@router.get("")
async def list_agents_endpoint(
    limit: int = Query(50, ge=1, le=1000),
    q: str | None = Query(None, max_length=128),
    gateway: Gateway = Depends(get_gateway),
) -> dict[str, Any]:
    """Return the fleet index over the last 24h: telemetry rollup + live roster.

    Telemetry-derived agents come from the ``agent_observations`` rollup (refreshed
    every 15 minutes) so cost / requests / p95 / error_rate all cover the same
    window. Registered workers from the live heartbeat roster are merged in, so a
    booted-but-idle agent (0 requests) still appears (with an idle/busy/offline
    ``fleet_status``), matching Server > Fleet. ``q`` is a substring match against
    agent_id; the unattributed bucket (NULL agent_id) is returned separately.
    """
    if gateway.storage is None:
        return {"agents": [], "unattributed": dict(_EMPTY_UNATTRIBUTED)}
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        rows = await agent_obs.read_agents(db, limit=limit, query=q)
        unattributed = await agent_obs.read_unattributed(db)
        # Live worker roster: per-agent memory headroom + idle/busy presence, and
        # the source for registered-but-idle agents. tenant_id=None = full fleet.
        roster = await workers_repository.read_roster(
            db, tenant_id=None, now=time.time(), ttl_seconds=DEFAULT_TTL_SECONDS
        )
        agent_ids = [r.agent_id for r in rows if r.agent_id]
        # The model stack is looked up for roster agents too, not just the rollup
        # ones, so a booted-but-idle agent that has only been probed still shows
        # which STT/LLM/TTS it runs. The model NAMES are not a rollup metric (unlike
        # cost/p95), so surfacing them from a probe does not skew the card; the
        # latency and cost lookups below stay real-traffic-only.
        roster_agent_ids = [rw.agent_id for rw in roster if rw.agent_id]
        cascade = await request_log_repository.read_last_seen_models(
            db, list({*agent_ids, *roster_agent_ids})
        )
        # Average STT/LLM/TTS first-byte latency over the same 24h window, for the
        # Overview cards' latency waterfall.
        latency_by_agent = await request_log_repository.read_avg_ttfb_by_modality(
            db, agent_ids, since=time.time() - 86400
        )
        # Last-observed LiveKit dispatch name per agent. Read for every agent
        # (not just the rollup ones) so a registered-but-idle worker that ran a
        # call in some earlier window still gets a working play button. Not
        # windowed for the same reason: an agent's dispatch name is a property
        # of how its worker registered, not of recent traffic.
        dispatch_names = await request_log_repository.read_last_seen_dispatch_name(db)
    # Dedup the roster by agent_id, keeping the FRESHEST heartbeat. read_roster is
    # ordered last_seen DESC, so the first row per id is freshest; setdefault keeps
    # it. The full-fleet read (tenant_id=None) can return >1 row for one agent_id
    # across tenants, so without this a both-sources agent would take the stalest
    # tenant's status/memory and a roster-only id would be emitted twice.
    roster_by_id: dict[str, RosterRow] = {}
    for rw in roster:
        roster_by_id.setdefault(rw.agent_id, rw)
    memory_by_agent = {
        aid: _memory_pct(w.memory_rss_bytes, w.memory_total_bytes)
        for aid, w in roster_by_id.items()
    }
    # The LiveKit dispatch name a live worker reported, keyed by agent_id. This is
    # the fallback the play button uses for an agent that has heartbeated but not
    # yet served an instrumented call, so the button need not wait for a first
    # call. It is the worker's dispatch_name, NOT its display agent_name: a worker
    # with no LiveKit dispatch (a Pipecat agent, dispatch_name None) is omitted, so
    # it is not offered a probe it could never answer. "" is kept (automatic
    # dispatch) and resolves to mode "automatic".
    roster_names = {
        aid: w.dispatch_name
        for aid, w in roster_by_id.items()
        if w.dispatch_name is not None
    }

    # Resolved once per request: reading creds is a filesystem/env lookup, and
    # the answer is the same for every card.
    livekit_ready = _livekit_configured()
    # How many distinct agents are on automatic dispatch (registered with no
    # agent_name). Two or more means a probe that creates a room can be answered
    # by any of them, which the eligibility reason has to admit. Counted across
    # BOTH sources, deduped by agent_id: an agent seen only in the live roster is
    # just as able to grab an anonymous room's job as one seen in past traffic,
    # and dropping either would understate the ambiguity about whose numbers came
    # back.
    automatic_ids = {aid for aid, n in dispatch_names.items() if n == ""}
    automatic_ids |= {aid for aid, n in roster_names.items() if n == ""}
    automatic_count = len(automatic_ids)

    def _probe_for(agent_id: str | None) -> dict[str, Any]:
        return _probe_block(
            agent_id,
            dispatch_names,
            roster_names,
            livekit_configured=livekit_ready,
            automatic_count=automatic_count,
        )

    agents_out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in rows:
        w = roster_by_id.get(r.agent_id) if r.agent_id else None
        agents_out.append(
            _agent_entry(
                r,
                memory_by_agent.get(r.agent_id) if r.agent_id else None,
                cascade.get(r.agent_id, {}) if r.agent_id else {},
                latency_by_agent.get(r.agent_id, {}) if r.agent_id else {},
                fleet_status=w.status if w is not None else None,
                last_seen=_merged_last_seen(
                    r.last_seen, w.last_seen if w is not None else None
                ),
                agent_name=w.agent_name if w is not None else None,
                probe=_probe_for(r.agent_id),
            )
        )
        if r.agent_id:
            seen.add(r.agent_id)

    # Registered workers with no telemetry rows yet (respect the q filter). Skip
    # offline roster-only workers: a dead process that never metered anything is
    # noise on the fleet index (telemetry agents still show regardless of status).
    ql = q.lower() if q else None
    for w in roster_by_id.values():
        if w.agent_id in seen or w.status == "offline":
            continue
        if ql is not None and ql not in w.agent_id.lower():
            continue
        agents_out.append(
            _roster_only_entry(w, _probe_for(w.agent_id), cascade.get(w.agent_id, {}))
        )

    return {
        "agents": agents_out[:limit],
        "unattributed": _unattributed_entry(unattributed),
    }


@router.post("/{agent_id}/probe")
async def probe_agent_endpoint(
    agent_id: str,
    _auth: None = Depends(require_scope(ADMIN_SCOPE)),
    gateway: Gateway = Depends(get_gateway),
) -> dict[str, Any]:
    """Place one real call to this agent and report its latency split and cost.

    Every number in the response was measured. End-to-end time comes from a
    synthetic client that speaks a fixed utterance and waits for audio back; the
    STT/LLM/TTS split and ``cost_usd`` come from the rows the agent itself wrote
    for the probe's room. Anything that could not be measured is null, never
    zero: an agent shipping telemetry to a remote collector writes no rows here,
    and reporting that as ``$0.00`` would be a false claim rather than a missing
    one.

    Admin-scoped (the gate is a no-op until API keys are configured) because the
    call is billed against real providers, and rate limited per agent for the
    same reason: 409 while one is in flight, 429 inside the cooldown.

    The probe's rows stay out of the agent's 24h rollups: they are tagged by a
    ``vg-probe-`` room name, which the rollup excludes, so pressing play cannot
    move the card's own cost, p95 or error rate.
    """
    if gateway.storage is None:
        raise HTTPException(
            status_code=400,
            detail="telemetry storage is disabled, so a probe could not be measured",
        )

    try:
        creds = _resolve_creds()
    except CredsError:
        raise HTTPException(status_code=400, detail="LiveKit not configured") from None

    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        names = await request_log_repository.read_last_seen_dispatch_name(
            db, [agent_id]
        )
        dispatch_name = names.get(agent_id)
        # Fall back to the live roster when no completed job carried a name. A
        # worker reports the LiveKit agent_name it dispatches under, so an agent
        # that has heartbeated but not yet served an instrumented call is still
        # reachable: it put the name here the moment it came online. read_roster
        # is ordered last_seen DESC, so the first match is the freshest row, the
        # same one the card's roster_names built from. A worker with no dispatch
        # name (Pipecat) yields None here and falls through to the 400 below. If
        # the name is wrong, the probe reaches no worker and the runner fails
        # fast with that reason; nothing is faked.
        if dispatch_name is None:
            roster = await workers_repository.read_roster(
                db, tenant_id=None, now=time.time(), ttl_seconds=DEFAULT_TTL_SECONDS
            )
            dispatch_name = next(
                (
                    w.dispatch_name
                    for w in roster
                    if w.agent_id == agent_id and w.dispatch_name is not None
                ),
                None,
            )
    if dispatch_name is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"no LiveKit job observed for agent {agent_id!r} and no live "
                "worker in the roster: VoiceGateway dispatches by the agent_name "
                "a worker registers with, and has seen neither"
            ),
        )

    now = time.monotonic()
    last = _PROBE_LAST_RUN.get(agent_id)
    if last is not None and now - last < PROBE_COOLDOWN_SECONDS:
        retry_after = int(PROBE_COOLDOWN_SECONDS - (now - last)) + 1
        # Carried on the exception, not on an injected Response: raising
        # discards the injected object, so that is the copy the client sees.
        raise HTTPException(
            status_code=429,
            detail=(
                f"a probe places a billed call; wait {retry_after}s before "
                "probing this agent again"
            ),
            headers={"Retry-After": str(retry_after)},
        )
    if agent_id in _PROBES_INFLIGHT:
        raise HTTPException(
            status_code=409, detail=f"a probe for {agent_id!r} is already running"
        )

    # Stamped before the call, not after: the cooldown is about how often calls
    # are placed, so a probe that hangs for the full timeout must not then allow
    # an immediate second one.
    _PROBE_LAST_RUN[agent_id] = now
    _PROBES_INFLIGHT.add(agent_id)
    try:
        return await asyncio.wait_for(
            diag_service.probe_agent(
                creds,
                agent_id=agent_id,
                dispatch_name=dispatch_name,
                nonce=uuid.uuid4().hex[:8],
                # No warmup turn: one press must place exactly one billed call,
                # which is what the button promises the operator. A warmup would
                # double the provider spend of a press to steady a single
                # sample, and the cost shown would then describe half of what
                # the press actually charged. The trade is accepted honestly:
                # this is one real call including whatever cold start it hit,
                # rendered next to (not merged into) the 24h average.
                warmup=False,
                store=gateway.storage,
            ),
            diag_service.PROBE_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        raise HTTPException(status_code=504, detail="probe timed out") from None
    finally:
        _PROBES_INFLIGHT.discard(agent_id)


@router.get("/{agent_id}")
async def get_agent_endpoint(
    agent_id: str, gateway: Gateway = Depends(get_gateway)
) -> dict[str, Any]:
    """Return aggregates for a single agent. 404 when unseen."""
    if gateway.storage is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id!r} not found")
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        row = await agents.get_agent(db, agent_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Agent {agent_id!r} not found")
        p95 = await agents.agent_latency_p95(db, agent_id=agent_id)
    entry = dataclasses.asdict(row)
    entry["p95_latency_ms"] = p95.get(agent_id)
    return entry
