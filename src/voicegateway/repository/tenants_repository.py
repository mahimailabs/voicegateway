"""Async read-side repo for the tenant index."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import aiosqlite


_DEFAULT_LIMIT = 50
_MAX_LIMIT = 1000


@dataclass(frozen=True)
class TenantRow:
    """One row in the tenant index (the dashboard's tenant filter feed)."""

    tenant_id: str
    session_count: int
    total_cost_usd: float
    first_seen: str | None
    last_seen: str | None


@dataclass(frozen=True)
class UnattributedAggregates:
    """Aggregates for the implicit ``tenant_id IS NULL`` bucket."""

    session_count: int
    total_cost_usd: float
    first_seen: str | None
    last_seen: str | None


def _row_to_tenant(row: Sequence[Any]) -> TenantRow:
    return TenantRow(
        tenant_id=str(row[0]),
        session_count=int(row[1]),
        total_cost_usd=float(row[2] or 0.0),
        first_seen=None if row[3] is None else str(row[3]),
        last_seen=None if row[4] is None else str(row[4]),
    )


_SELECT_AGGREGATES = """
SELECT tenant_id,
       COUNT(*) AS session_count,
       COALESCE(SUM(total_cost_usd), 0.0) AS total_cost_usd,
       MIN(started_at) AS first_seen,
       MAX(COALESCE(ended_at, started_at)) AS last_seen
FROM sessions
WHERE tenant_id IS NOT NULL
"""


async def list_tenants(
    db: aiosqlite.Connection,
    *,
    limit: int = _DEFAULT_LIMIT,
    query: str | None = None,
) -> list[TenantRow]:
    """Return the tenant index, ordered by ``last_seen`` descending."""
    if limit < 1:
        limit = 1
    if limit > _MAX_LIMIT:
        limit = _MAX_LIMIT

    clauses: list[str] = []
    params: list[Any] = []
    if query:
        clauses.append("AND tenant_id LIKE ? ESCAPE '\\'")

        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.append(f"%{escaped}%")

    sql = _SELECT_AGGREGATES
    if clauses:
        sql += " " + " ".join(clauses) + " "
    sql += " GROUP BY tenant_id  ORDER BY last_seen DESC, tenant_id ASC  LIMIT ?"
    params.append(limit)

    cursor = await db.execute(sql, params)
    return [_row_to_tenant(row) async for row in cursor]


async def get_tenant(db: aiosqlite.Connection, tenant_id: str) -> TenantRow | None:
    """Return aggregates for a single tenant or ``None`` if not seen."""
    cursor = await db.execute(
        _SELECT_AGGREGATES + " AND tenant_id = ? " + "GROUP BY tenant_id",
        (tenant_id,),
    )
    row = await cursor.fetchone()
    return _row_to_tenant(row) if row is not None else None


async def count_tenants(db: aiosqlite.Connection) -> int:
    """Return the number of distinct attributed tenants in the index."""
    cursor = await db.execute(
        "SELECT COUNT(DISTINCT tenant_id) FROM sessions WHERE tenant_id IS NOT NULL"
    )
    row = await cursor.fetchone()
    return int(row[0]) if row is not None else 0


async def get_unattributed_aggregates(
    db: aiosqlite.Connection,
) -> UnattributedAggregates:
    """Return aggregates for the ``tenant_id IS NULL`` bucket."""
    cursor = await db.execute(
        """SELECT COUNT(*) AS session_count,
                  COALESCE(SUM(total_cost_usd), 0.0) AS total_cost_usd,
                  MIN(started_at) AS first_seen,
                  MAX(COALESCE(ended_at, started_at)) AS last_seen
           FROM sessions
           WHERE tenant_id IS NULL"""
    )
    row = await cursor.fetchone()
    if row is None:
        return UnattributedAggregates(
            session_count=0, total_cost_usd=0.0, first_seen=None, last_seen=None
        )
    return UnattributedAggregates(
        session_count=int(row[0]),
        total_cost_usd=float(row[1] or 0.0),
        first_seen=None if row[2] is None else str(row[2]),
        last_seen=None if row[3] is None else str(row[3]),
    )


__all__ = [
    "TenantRow",
    "UnattributedAggregates",
    "count_tenants",
    "get_tenant",
    "get_unattributed_aggregates",
    "list_tenants",
]
