"""Async repo for the cost-aggregation queries against the requests table."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import aiosqlite


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
    include_project: bool = True,
) -> tuple[str, list[Any]]:
    """Compose the WHERE clause + bound params for a window-+-filter query."""
    params: list[Any] = [since]
    where = "WHERE timestamp >= ?"
    if until is not None:
        where += " AND timestamp < ?"
        params.append(until)
    if include_project and project:
        where += " AND project = ?"
        params.append(project)
    if tenant is not None:
        if tenant == "":
            where += " AND tenant_id IS NULL"
        else:
            where += " AND tenant_id = ?"
            params.append(tenant)
    return where, params


async def get_cost_summary(
    db: aiosqlite.Connection,
    period: str = "today",
    project: str | None = None,
    include_pricing_source: bool = False,
    start_ts: float | None = None,
    end_ts: float | None = None,
    tenant: str | None = None,
) -> dict[str, Any]:
    """Return total / by_provider / by_model cost rollups for the window."""
    since, until = resolve_window(period, start_ts, end_ts)
    where, params = _build_where(
        since=since, until=until, project=project, tenant=tenant
    )

    cursor = await db.execute(
        f"SELECT COALESCE(SUM(cost_usd), 0) FROM requests {where}",
        tuple(params),
    )
    row = await cursor.fetchone()
    total = row[0] if row else 0.0

    cursor = await db.execute(
        f"""SELECT provider, SUM(cost_usd) as cost, COUNT(*) as count
            FROM requests {where}
            GROUP BY provider ORDER BY cost DESC""",
        tuple(params),
    )
    by_provider = {row[0]: {"cost": row[1], "requests": row[2]} async for row in cursor}

    if include_pricing_source:
        cursor = await db.execute(
            f"""SELECT model_id, SUM(cost_usd) as cost, COUNT(*) as count,
                       GROUP_CONCAT(DISTINCT pricing_source) as sources
                FROM requests {where}
                GROUP BY model_id ORDER BY cost DESC""",
            tuple(params),
        )
        by_model = {
            row[0]: {
                "cost": row[1],
                "requests": row[2],
                "pricing_source": row[3] or "",
            }
            async for row in cursor
        }
    else:
        cursor = await db.execute(
            f"""SELECT model_id, SUM(cost_usd) as cost, COUNT(*) as count
                FROM requests {where}
                GROUP BY model_id ORDER BY cost DESC""",
            tuple(params),
        )
        by_model = {
            row[0]: {"cost": row[1], "requests": row[2]} async for row in cursor
        }

    return {
        "period": period,
        "project": project,
        "total": total,
        "by_provider": by_provider,
        "by_model": by_model,
    }


async def get_cost_by_project(
    db: aiosqlite.Connection,
    period: str = "today",
    start_ts: float | None = None,
    end_ts: float | None = None,
    tenant: str | None = None,
) -> dict[str, Any]:
    """Return cost rollup grouped by project."""
    since, until = resolve_window(period, start_ts, end_ts)
    where, params = _build_where(
        since=since,
        until=until,
        project=None,
        tenant=tenant,
        include_project=False,
    )
    cursor = await db.execute(
        f"""SELECT project, SUM(cost_usd) as cost, COUNT(*) as count
            FROM requests {where}
            GROUP BY project ORDER BY cost DESC""",
        tuple(params),
    )
    return {row[0]: {"cost": row[1], "requests": row[2]} async for row in cursor}


async def get_cost_by_modality(
    db: aiosqlite.Connection,
    period: str = "today",
    project: str | None = None,
    start_ts: float | None = None,
    end_ts: float | None = None,
) -> dict[str, Any]:
    """Return cost rollup grouped by modality (stt/llm/tts)."""
    since, until = resolve_window(period, start_ts, end_ts)
    where, params = _build_where(since=since, until=until, project=project, tenant=None)
    cursor = await db.execute(
        f"""SELECT modality, SUM(cost_usd) as cost, COUNT(*) as count
            FROM requests {where}
            GROUP BY modality ORDER BY cost DESC""",
        tuple(params),
    )
    return {row[0]: {"cost": row[1], "requests": row[2]} async for row in cursor}


async def get_project_stats(db: aiosqlite.Connection, project: str) -> dict[str, Any]:
    """Per-project today snapshot: requests, cost, average ttfb + latency."""
    since_today = period_since("today")
    cursor = await db.execute(
        """SELECT
               COUNT(*),
               COALESCE(SUM(cost_usd), 0),
               AVG(ttfb_ms),
               AVG(total_latency_ms)
           FROM requests
           WHERE project = ? AND timestamp >= ?""",
        (project, since_today),
    )
    row = await cursor.fetchone()
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
