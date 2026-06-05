"""Async repo for the ``agent_observations`` rollup table (fleet dashboard).

``roll_up`` mirrors ``latency_observations_repository.roll_up``: it scans the
``requests`` table over the trailing window, groups by ``agent_id`` (None is
the unattributed bucket), computes the per-agent aggregates in Python, and
replaces the table wholesale (DELETE then INSERT) in a single transaction so a
concurrent reader never sees a half-empty table. The read paths serve the
fleet list (agent_id IS NOT NULL) and the unattributed bucket (agent_id IS
NULL); error_rate is derived by the caller from error_count / request_count.

All SQL is plain parameterized CRUD (no INSTR, date bucketing, or boolean SUM),
so the table is portable across SQLite and Postgres with no dialect branching.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from voicegateway.utils.percentiles import compute_percentiles

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_DEFAULT_WINDOW_MINUTES = 24 * 60  # 24h rolling window, matching Routing.
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 1000


@dataclass(frozen=True)
class AgentObservationRow:
    """One row from the agent_observations rollup snapshot."""

    agent_id: str | None
    request_count: int
    total_cost_usd: float
    error_count: int
    p50_ms: int | None
    p95_ms: int | None
    last_seen: float | None
    window_start: str
    window_end: str
    refreshed_at: str


_SELECT_FIELDS = (
    "agent_id, request_count, total_cost_usd, error_count, p50_ms, p95_ms, "
    "last_seen, window_start, window_end, refreshed_at"
)


def _row(r: Sequence[Any]) -> AgentObservationRow:
    return AgentObservationRow(
        agent_id=None if r[0] is None else str(r[0]),
        request_count=int(r[1]),
        total_cost_usd=float(r[2] or 0.0),
        error_count=int(r[3] or 0),
        p50_ms=None if r[4] is None else int(r[4]),
        p95_ms=None if r[5] is None else int(r[5]),
        last_seen=None if r[6] is None else float(r[6]),
        window_start=str(r[7]),
        window_end=str(r[8]),
        refreshed_at=str(r[9]),
    )


async def roll_up(
    session: AsyncSession, *, window_minutes: int = _DEFAULT_WINDOW_MINUTES
) -> int:
    """Recompute the per-agent rollup over the trailing window. Returns rows."""
    now = _dt.datetime.now(tz=_dt.UTC)
    window_start = now - _dt.timedelta(minutes=window_minutes)
    ws_ts = window_start.timestamp()
    we_ts = now.timestamp()

    result = await session.execute(
        text(
            "SELECT agent_id, cost_usd, status, total_latency_ms, timestamp "
            "FROM requests WHERE timestamp >= :ws AND timestamp < :we"
        ),
        {"ws": ws_ts, "we": we_ts},
    )

    groups: dict[Any, dict[str, Any]] = {}
    for agent_id, cost, status, latency, ts in result.fetchall():
        g = groups.get(agent_id)
        if g is None:
            g = {"count": 0, "cost": 0.0, "errors": 0, "last": None, "lat": []}
            groups[agent_id] = g
        g["count"] += 1
        g["cost"] += float(cost or 0.0)
        if status == "error":
            g["errors"] += 1
        if ts is not None:
            tsf = float(ts)
            if g["last"] is None or tsf > g["last"]:
                g["last"] = tsf
        if latency is not None:
            g["lat"].append(float(latency))

    await session.execute(text("DELETE FROM agent_observations"))

    insert = text(
        "INSERT INTO agent_observations "
        "(agent_id, request_count, total_cost_usd, error_count, p50_ms, p95_ms, "
        " last_seen, window_start, window_end) "
        "VALUES (:agent_id, :rc, :cost, :errors, :p50, :p95, :last, :ws, :we)"
    )
    inserted = 0
    for agent_id, g in groups.items():
        pcts = compute_percentiles(g["lat"], [50.0, 95.0])
        p50 = pcts.get("p50")
        p95 = pcts.get("p95")
        await session.execute(
            insert,
            {
                "agent_id": agent_id,
                "rc": g["count"],
                "cost": g["cost"],
                "errors": g["errors"],
                "p50": None if p50 is None else int(round(p50)),
                "p95": None if p95 is None else int(round(p95)),
                "last": g["last"],
                "ws": window_start.isoformat(),
                "we": now.isoformat(),
            },
        )
        inserted += 1

    await session.commit()
    return inserted


async def read_agents(
    session: AsyncSession, *, limit: int = _DEFAULT_LIMIT, query: str | None = None
) -> list[AgentObservationRow]:
    """Return attributed agents (agent_id IS NOT NULL), newest first."""
    limit = max(1, min(limit, _MAX_LIMIT))
    sql = f"SELECT {_SELECT_FIELDS} FROM agent_observations WHERE agent_id IS NOT NULL"
    params: dict[str, Any] = {"limit": limit}
    if query:
        sql += r" AND agent_id LIKE :pattern ESCAPE '\'"
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params["pattern"] = f"%{escaped}%"
    sql += " ORDER BY last_seen DESC, agent_id ASC LIMIT :limit"
    result = await session.execute(text(sql), params)
    return [_row(r) for r in result]


async def read_unattributed(session: AsyncSession) -> AgentObservationRow | None:
    """Return the agent_id IS NULL bucket row, or None if it is empty."""
    result = await session.execute(
        text(f"SELECT {_SELECT_FIELDS} FROM agent_observations WHERE agent_id IS NULL")
    )
    row = result.fetchone()
    return _row(row) if row is not None else None


__all__ = [
    "AgentObservationRow",
    "read_agents",
    "read_unattributed",
    "roll_up",
]
