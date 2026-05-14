"""Async repo for latency aggregation queries against the requests table (ORM).

The two queries (aggregate-then-samples) stay as ``text()`` clauses
because the two-cursor pattern is the algorithm: pull AVG + COUNT in
one pass, then pull raw samples to compute percentiles in Python via
:func:`voicegateway.utils.percentiles.compute_percentiles`. The two
cursors run sequentially within the same session so the bucketing
stays consistent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from voicegateway.repository.cost_repository import period_since
from voicegateway.utils.percentiles import compute_percentiles

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_DEFAULT_PERCENTILES: list[float] = [50.0, 95.0, 99.0]


async def get_latency_stats(
    session: AsyncSession,
    period: str = "today",
    project: str | None = None,
    percentiles: list[float] | None = None,
    tenant: str | None = None,
) -> dict[str, Any]:
    """Per-model latency rollup with avg + percentile distributions."""
    pcts = percentiles or _DEFAULT_PERCENTILES
    since = period_since(period)
    params: dict[str, Any] = {"since": since}
    where = (
        "WHERE timestamp >= :since "
        "AND (ttfb_ms IS NOT NULL OR total_latency_ms IS NOT NULL)"
    )
    if project:
        where += " AND project = :project"
        params["project"] = project
    if tenant is not None:
        if tenant == "":
            where += " AND tenant_id IS NULL"
        else:
            where += " AND tenant_id = :tenant"
            params["tenant"] = tenant

    result = await session.execute(
        text(
            f"""SELECT model_id,
                       AVG(ttfb_ms) as avg_ttfb,
                       AVG(total_latency_ms) as avg_latency,
                       COUNT(*) as count
                FROM requests
                {where}
                GROUP BY model_id"""
        ),
        params,
    )
    stats: dict[str, dict[str, Any]] = {}
    for row in result:
        stats[row[0]] = {
            "avg_ttfb_ms": row[1],
            "avg_latency_ms": row[2],
            "request_count": row[3],
            "ttfb_percentiles": compute_percentiles([], pcts),
            "latency_percentiles": compute_percentiles([], pcts),
        }

    if not stats:
        return stats

    sample_result = await session.execute(
        text(
            f"""SELECT model_id, ttfb_ms, total_latency_ms
                FROM requests
                {where}"""
        ),
        params,
    )
    ttfb_by_model: dict[str, list[float]] = {}
    lat_by_model: dict[str, list[float]] = {}
    for row in sample_result:
        model_id = row[0]
        if row[1] is not None:
            ttfb_by_model.setdefault(model_id, []).append(float(row[1]))
        if row[2] is not None:
            lat_by_model.setdefault(model_id, []).append(float(row[2]))

    for model_id, entry in stats.items():
        entry["ttfb_percentiles"] = compute_percentiles(
            ttfb_by_model.get(model_id, []), pcts
        )
        entry["latency_percentiles"] = compute_percentiles(
            lat_by_model.get(model_id, []), pcts
        )

    return stats


async def get_latency_samples(
    session: AsyncSession,
    period: str = "today",
    project: str | None = None,
    modality: str | None = None,
) -> tuple[list[float], list[float]]:
    """Return ``(ttfb_samples, total_latency_samples)`` for the window."""
    since = period_since(period)
    params: dict[str, Any] = {"since": since}
    where = "WHERE timestamp >= :since"
    if project:
        where += " AND project = :project"
        params["project"] = project
    if modality:
        where += " AND modality = :modality"
        params["modality"] = modality

    result = await session.execute(
        text(
            f"""SELECT ttfb_ms, total_latency_ms
                FROM requests
                {where}"""
        ),
        params,
    )
    ttfb: list[float] = []
    total: list[float] = []
    for row in result:
        if row[0] is not None:
            ttfb.append(float(row[0]))
        if row[1] is not None:
            total.append(float(row[1]))
    return ttfb, total


__all__ = ["get_latency_samples", "get_latency_stats"]
