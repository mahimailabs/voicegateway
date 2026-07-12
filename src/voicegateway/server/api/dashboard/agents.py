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


def _agent_entry(
    row: AgentObservationRow, memory_pct: float | None, models: dict[str, str]
) -> dict[str, Any]:
    return {
        "agent_id": row.agent_id,
        "request_count": row.request_count,
        "total_cost_usd": row.total_cost_usd,
        "last_seen": row.last_seen,
        "error_rate": _error_rate(row.error_count, row.request_count),
        "p95_latency_ms": row.p95_ms,
        "memory_pct": memory_pct,
        "models": {
            "stt": models.get("stt"),
            "llm": models.get("llm"),
            "tts": models.get("tts"),
        },
    }


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
    """Return the fleet index over the last 24h, read from the rollup.

    Served from the ``agent_observations`` rollup (refreshed every 15 minutes),
    not a live requests scan, so cost / requests / p95 / error_rate all cover the
    same window. ``q`` is a substring match against agent_id; the unattributed
    bucket (NULL agent_id) is returned separately.
    """
    if gateway.storage is None:
        return {"agents": [], "unattributed": dict(_EMPTY_UNATTRIBUTED)}
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        rows = await agent_obs.read_agents(db, limit=limit, query=q)
        unattributed = await agent_obs.read_unattributed(db)
        # Live worker roster carries per-agent memory headroom. tenant_id=None
        # returns the full fleet, which is what the self-hosted dashboard wants.
        roster = await workers_repository.read_roster(
            db, tenant_id=None, now=time.time(), ttl_seconds=DEFAULT_TTL_SECONDS
        )
        agent_ids = [r.agent_id for r in rows if r.agent_id]
        cascade = await request_log_repository.read_last_seen_models(db, agent_ids)
    memory_by_agent = {
        r.agent_id: _memory_pct(r.memory_rss_bytes, r.memory_total_bytes)
        for r in roster
    }
    return {
        "agents": [
            _agent_entry(
                r,
                memory_by_agent.get(r.agent_id) if r.agent_id else None,
                cascade.get(r.agent_id, {}) if r.agent_id else {},
            )
            for r in rows
        ],
        "unattributed": _unattributed_entry(unattributed),
    }


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
