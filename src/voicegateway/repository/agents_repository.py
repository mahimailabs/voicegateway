"""Async read-side repo for the agent index (fleet dashboard).

Mirrors tenants_repository, but aggregates over the ``requests`` table (which
carries ``agent_id`` from Phase 1) rather than ``sessions``: per-agent request
count, total cost, last-seen (max request timestamp, epoch seconds), and error
rate. Per-agent p95 latency is a separate windowed fetch, grouped and
percentiled in Python, so the index endpoint stays O(1) queries instead of one
percentile query per agent.

The error-rate division guards against a zero denominator with
``NULLIF(COUNT(*), 0)`` (Postgres raises on divide-by-zero; the grouped queries
never hit it, but the unattributed bucket can when there are no NULL rows).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from voicegateway.utils.percentiles import compute_percentiles

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


_DEFAULT_LIMIT = 50
_MAX_LIMIT = 1000


@dataclass(frozen=True)
class AgentRow:
    """One row in the agent index (the fleet table + agent filter feed)."""

    agent_id: str
    request_count: int
    total_cost_usd: float
    last_seen: float | None  # epoch seconds of the most recent request
    error_rate: float


@dataclass(frozen=True)
class UnattributedAggregates:
    """Aggregates for the implicit ``agent_id IS NULL`` bucket."""

    request_count: int
    total_cost_usd: float
    last_seen: float | None
    error_rate: float


def _row_to_agent(row: Sequence[Any]) -> AgentRow:
    return AgentRow(
        agent_id=str(row[0]),
        request_count=int(row[1]),
        total_cost_usd=float(row[2] or 0.0),
        last_seen=None if row[3] is None else float(row[3]),
        error_rate=float(row[4] or 0.0),
    )


_ERROR_RATE = (
    "(SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) * 1.0) / NULLIF(COUNT(*), 0)"
)

_SELECT_AGGREGATES = f"""
SELECT agent_id,
       COUNT(*) AS request_count,
       COALESCE(SUM(cost_usd), 0.0) AS total_cost_usd,
       MAX(timestamp) AS last_seen,
       {_ERROR_RATE} AS error_rate
FROM requests
WHERE agent_id IS NOT NULL
"""


async def list_agents(
    session: AsyncSession,
    *,
    limit: int = _DEFAULT_LIMIT,
    query: str | None = None,
) -> list[AgentRow]:
    """Return the agent index, ordered by ``last_seen`` descending."""
    if limit < 1:
        limit = 1
    if limit > _MAX_LIMIT:
        limit = _MAX_LIMIT

    sql = _SELECT_AGGREGATES
    params: dict[str, Any] = {"limit": limit}
    if query:
        sql += r" AND agent_id LIKE :pattern ESCAPE '\'"
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params["pattern"] = f"%{escaped}%"
    sql += " GROUP BY agent_id ORDER BY last_seen DESC, agent_id ASC LIMIT :limit"

    result = await session.execute(text(sql), params)
    return [_row_to_agent(row) for row in result]


async def get_agent(session: AsyncSession, agent_id: str) -> AgentRow | None:
    """Return aggregates for a single agent or ``None`` if not seen."""
    sql = _SELECT_AGGREGATES + " AND agent_id = :agent_id GROUP BY agent_id"
    result = await session.execute(text(sql), {"agent_id": agent_id})
    row = result.fetchone()
    return _row_to_agent(row) if row is not None else None


async def get_unattributed_aggregates(
    session: AsyncSession,
) -> UnattributedAggregates:
    """Return aggregates for the ``agent_id IS NULL`` bucket."""
    result = await session.execute(
        text(
            f"""SELECT COUNT(*) AS request_count,
                       COALESCE(SUM(cost_usd), 0.0) AS total_cost_usd,
                       MAX(timestamp) AS last_seen,
                       {_ERROR_RATE} AS error_rate
                FROM requests
                WHERE agent_id IS NULL"""
        )
    )
    row = result.fetchone()
    if row is None or int(row[0]) == 0:
        return UnattributedAggregates(
            request_count=0, total_cost_usd=0.0, last_seen=None, error_rate=0.0
        )
    return UnattributedAggregates(
        request_count=int(row[0]),
        total_cost_usd=float(row[1] or 0.0),
        last_seen=None if row[2] is None else float(row[2]),
        error_rate=float(row[3] or 0.0),
    )


async def agent_latency_p95(
    session: AsyncSession, *, since: float | None = None
) -> dict[str, float]:
    """Return ``{agent_id: p95 total-latency ms}`` via one fetch + grouping."""
    where = "WHERE agent_id IS NOT NULL AND total_latency_ms IS NOT NULL"
    params: dict[str, Any] = {}
    if since is not None:
        where += " AND timestamp >= :since"
        params["since"] = since
    result = await session.execute(
        text(f"SELECT agent_id, total_latency_ms FROM requests {where}"), params
    )
    by_agent: dict[str, list[float]] = {}
    for row in result:
        by_agent.setdefault(str(row[0]), []).append(float(row[1]))
    out: dict[str, float] = {}
    for agent_id, samples in by_agent.items():
        p95 = compute_percentiles(samples, [95.0]).get("p95")
        if p95 is not None:
            out[agent_id] = float(p95)
    return out


__all__ = [
    "AgentRow",
    "UnattributedAggregates",
    "agent_latency_p95",
    "get_agent",
    "get_unattributed_aggregates",
    "list_agents",
]
