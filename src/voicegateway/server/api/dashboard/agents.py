"""Dashboard endpoints: GET /api/agents, /api/agents/{agent_id}.

Phase 2 fleet dashboard. Agents are derived from DISTINCT requests.agent_id;
this index feeds the Agents page (fleet table) and the agent-filter typeahead.
Per-agent p95 latency is merged in from a single windowed query so the index
stays O(1) queries.
"""

from __future__ import annotations

import dataclasses
import time
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from voicegateway.repository import agent_observations_repository as agent_obs
from voicegateway.repository import agents_repository as agents
from voicegateway.repository import request_log_repository, workers_repository
from voicegateway.repository.workers_repository import DEFAULT_TTL_SECONDS
from voicegateway.server.api._deps import get_gateway

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


def _agent_entry(
    row: AgentObservationRow,
    memory_pct: float | None,
    models: dict[str, str],
    latency: dict[str, float],
    *,
    fleet_status: str | None = None,
    last_seen: float | None = None,
    agent_name: str | None = None,
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
    }


def _roster_only_entry(w: RosterRow) -> dict[str, Any]:
    """A registered worker that has not written any telemetry yet (0 requests).

    Lets a booted-but-idle agent show on the Agents page (matching Server > Fleet)
    instead of appearing only once it has handled a call.
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
        "models": {"stt": None, "llm": None, "tts": None},
        # A booted-but-idle worker has metered nothing yet, so no latency stack.
        "latency_ms": {"stt": None, "llm": None, "tts": None},
        "fleet_status": w.status,
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
        cascade = await request_log_repository.read_last_seen_models(db, agent_ids)
        # Average STT/LLM/TTS first-byte latency over the same 24h window, for the
        # Overview cards' latency waterfall.
        latency_by_agent = await request_log_repository.read_avg_ttfb_by_modality(
            db, agent_ids, since=time.time() - 86400
        )
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
        agents_out.append(_roster_only_entry(w))

    return {"agents": agents_out[:limit], "unattributed": _unattributed_entry(unattributed)}


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
