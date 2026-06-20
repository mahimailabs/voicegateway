"""Async repo for cost-aggregation queries against the requests table (ORM).

The aggregation SQL stays as ``text()`` clauses because the WHERE-clause
composition + the ``GROUP_CONCAT(DISTINCT pricing_source)`` path in
``get_cost_summary`` were tested and tuned in the legacy aiosqlite
form. Translating each ``GROUP BY`` to ORM ``select(...).group_by(...)``
buys us little: the dict-shape post-processing dominates the function.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def period_since(period: str) -> float:
    """Return a UTC timestamp N days before now for the named period."""
    now = time.time()
    if period == "today":
        return now - 86400
    if period == "week":
        return now - 7 * 86400
    if period == "month":
        return now - 30 * 86400
    return 0


def resolve_window(
    period: str = "today",
    start_ts: float | None = None,
    end_ts: float | None = None,
) -> tuple[float, float | None]:
    """Resolve a query window into a ``(since, until)`` timestamp pair."""
    if start_ts is not None or end_ts is not None:
        return (start_ts if start_ts is not None else 0.0, end_ts)
    return (period_since(period), None)


def _build_where(
    *,
    since: float,
    until: float | None,
    project: str | None,
    tenant: str | None,
    agent: str | None = None,
    include_project: bool = True,
) -> tuple[str, dict[str, Any]]:
    """Compose the WHERE clause + named params for a window-+-filter query."""
    params: dict[str, Any] = {"since": since}
    where = "WHERE timestamp >= :since"
    if until is not None:
        where += " AND timestamp < :until"
        params["until"] = until
    if include_project and project:
        where += " AND project = :project"
        params["project"] = project
    if tenant is not None:
        if tenant == "":
            where += " AND tenant_id IS NULL"
        else:
            where += " AND tenant_id = :tenant"
            params["tenant"] = tenant
    if agent is not None:
        if agent == "":
            where += " AND agent_id IS NULL"
        else:
            where += " AND agent_id = :agent"
            params["agent"] = agent
    return where, params


async def get_cost_summary(
    session: AsyncSession,
    period: str = "today",
    project: str | None = None,
    include_pricing_source: bool = False,
    start_ts: float | None = None,
    end_ts: float | None = None,
    tenant: str | None = None,
    agent: str | None = None,
) -> dict[str, Any]:
    """Return total / by_provider / by_model cost rollups for the window."""
    since, until = resolve_window(period, start_ts, end_ts)
    where, params = _build_where(
        since=since, until=until, project=project, tenant=tenant, agent=agent
    )

    result = await session.execute(
        text(f"SELECT COALESCE(SUM(cost_usd), 0) FROM requests {where}"), params
    )
    row = result.fetchone()
    total = row[0] if row else 0.0

    result = await session.execute(
        text(
            f"""SELECT provider, SUM(cost_usd) as cost, COUNT(*) as count
                FROM requests {where}
                GROUP BY provider ORDER BY cost DESC"""
        ),
        params,
    )
    by_provider = {r[0]: {"cost": r[1], "requests": r[2]} for r in result}

    if include_pricing_source:
        # GROUP_CONCAT is SQLite-only; Postgres spells the same aggregate
        # STRING_AGG(expr, sep). Pick by the bound dialect (default SQLite).
        try:
            dialect = session.bind.dialect.name
        except Exception:  # noqa: BLE001 - default to the SQLite spelling
            dialect = "sqlite"
        sources_agg = (
            "STRING_AGG(DISTINCT pricing_source, ',')"
            if dialect == "postgresql"
            else "GROUP_CONCAT(DISTINCT pricing_source)"
        )
        result = await session.execute(
            text(
                f"""SELECT model_id, SUM(cost_usd) as cost, COUNT(*) as count,
                           {sources_agg} as sources
                    FROM requests {where}
                    GROUP BY model_id ORDER BY cost DESC"""
            ),
            params,
        )
        by_model = {
            r[0]: {
                "cost": r[1],
                "requests": r[2],
                "pricing_source": r[3] or "",
            }
            for r in result
        }
    else:
        result = await session.execute(
            text(
                f"""SELECT model_id, SUM(cost_usd) as cost, COUNT(*) as count
                    FROM requests {where}
                    GROUP BY model_id ORDER BY cost DESC"""
            ),
            params,
        )
        by_model = {r[0]: {"cost": r[1], "requests": r[2]} for r in result}

    return {
        "period": period,
        "project": project,
        "total": total,
        "by_provider": by_provider,
        "by_model": by_model,
    }


async def get_cost_by_project(
    session: AsyncSession,
    period: str = "today",
    start_ts: float | None = None,
    end_ts: float | None = None,
    tenant: str | None = None,
    agent: str | None = None,
) -> dict[str, Any]:
    """Return cost rollup grouped by project."""
    since, until = resolve_window(period, start_ts, end_ts)
    where, params = _build_where(
        since=since,
        until=until,
        project=None,
        tenant=tenant,
        agent=agent,
        include_project=False,
    )
    result = await session.execute(
        text(
            f"""SELECT project, SUM(cost_usd) as cost, COUNT(*) as count
                FROM requests {where}
                GROUP BY project ORDER BY cost DESC"""
        ),
        params,
    )
    return {r[0]: {"cost": r[1], "requests": r[2]} for r in result}


async def get_cost_by_modality(
    session: AsyncSession,
    period: str = "today",
    project: str | None = None,
    start_ts: float | None = None,
    end_ts: float | None = None,
) -> dict[str, Any]:
    """Return cost rollup grouped by modality (stt/llm/tts)."""
    since, until = resolve_window(period, start_ts, end_ts)
    where, params = _build_where(since=since, until=until, project=project, tenant=None)
    result = await session.execute(
        text(
            f"""SELECT modality, SUM(cost_usd) as cost, COUNT(*) as count
                FROM requests {where}
                GROUP BY modality ORDER BY cost DESC"""
        ),
        params,
    )
    return {r[0]: {"cost": r[1], "requests": r[2]} for r in result}


async def get_project_stats(session: AsyncSession, project: str) -> dict[str, Any]:
    """Per-project today snapshot: requests, cost, avg ttfb + latency."""
    since_today = period_since("today")
    result = await session.execute(
        text(
            """SELECT COUNT(*),
                      COALESCE(SUM(cost_usd), 0),
                      AVG(ttfb_ms),
                      AVG(total_latency_ms)
               FROM requests
               WHERE project = :project AND timestamp >= :since"""
        ),
        {"project": project, "since": since_today},
    )
    row = result.fetchone()
    if row is None:
        return {
            "project": project,
            "requests_today": 0,
            "cost_today": 0.0,
            "avg_ttfb_ms": None,
            "avg_latency_ms": None,
        }
    return {
        "project": project,
        "requests_today": int(row[0] or 0),
        "cost_today": float(row[1] or 0.0),
        "avg_ttfb_ms": float(row[2]) if row[2] is not None else None,
        "avg_latency_ms": float(row[3]) if row[3] is not None else None,
    }


__all__ = [
    "get_cost_by_modality",
    "get_cost_by_project",
    "get_cost_summary",
    "get_project_stats",
    "period_since",
    "resolve_window",
]
