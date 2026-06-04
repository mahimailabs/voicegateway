"""Dashboard endpoints: GET /api/agents, /api/agents/{agent_id}.

Phase 2 fleet dashboard. Agents are derived from DISTINCT requests.agent_id;
this index feeds the Agents page (fleet table) and the agent-filter typeahead.
Per-agent p95 latency is merged in from a single windowed query so the index
stays O(1) queries.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from voicegateway.repository import agents_repository as agents
from voicegateway.server.api._deps import get_gateway

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

router = APIRouter(prefix="/agents", tags=["dashboard"])

_EMPTY_UNATTRIBUTED = {
    "request_count": 0,
    "total_cost_usd": 0.0,
    "last_seen": None,
    "error_rate": 0.0,
}


@router.get("")
async def list_agents_endpoint(
    limit: int = Query(50, ge=1, le=1000),
    q: str | None = Query(None, max_length=128),
    gateway: Gateway = Depends(get_gateway),
) -> dict[str, Any]:
    """Return the agent index for the fleet table + filter typeahead.

    ``q`` is a substring match against agent_id. The implicit unattributed
    bucket (NULL agent_id) is returned separately so the FE can render it as a
    muted row. Each agent carries ``p95_latency_ms`` (null when no samples).
    """
    if gateway.storage is None:
        return {"agents": [], "unattributed": dict(_EMPTY_UNATTRIBUTED)}
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        rows = await agents.list_agents(db, limit=limit, query=q)
        unattributed = await agents.get_unattributed_aggregates(db)
        p95 = await agents.agent_latency_p95(db)
    out: list[dict[str, Any]] = []
    for row in rows:
        entry = dataclasses.asdict(row)
        entry["p95_latency_ms"] = p95.get(row.agent_id)
        out.append(entry)
    return {"agents": out, "unattributed": dataclasses.asdict(unattributed)}


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
        p95 = await agents.agent_latency_p95(db)
    entry = dataclasses.asdict(row)
    entry["p95_latency_ms"] = p95.get(agent_id)
    return entry
