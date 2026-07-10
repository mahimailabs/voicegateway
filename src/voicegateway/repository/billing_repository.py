"""Async repo for billable-usage aggregation against the requests table.

VoiceGateway rates each request at write time (``rated_price_usd``); these
queries roll rated revenue, recorded cost, and margin up per tenant for a
billing window. A parametric window (arbitrary start/end) is used instead of
a fixed daily view so a caller can bill on any customer cycle.

Reuses ``resolve_window`` + ``_build_where`` from :mod:`cost_repository` so
window and tenant-filter semantics (``tenant=""`` -> ``tenant_id IS NULL``)
match the cost queries exactly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from voicegateway.repository.cost_repository import _build_where, resolve_window

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _margin_pct(margin: float, rated: float) -> float:
    """Margin as a percentage of rated revenue (0 when nothing is billable)."""
    if rated == 0:
        return 0.0
    return (margin / rated) * 100.0


async def get_billable_usage(
    session: AsyncSession,
    *,
    period: str = "month",
    start_ts: float | None = None,
    end_ts: float | None = None,
    project: str | None = None,
    tenant: str | None = None,
) -> list[dict[str, Any]]:
    """Return one rolled-up billing row per tenant for the window.

    Each row carries recorded cost, rated revenue, and the margin between
    them. Rows are ordered by rated revenue descending.
    """
    since, until = resolve_window(period, start_ts, end_ts)
    where, params = _build_where(
        since=since, until=until, project=project, tenant=tenant
    )
    result = await session.execute(
        text(
            f"""SELECT tenant_id,
                       COUNT(*) AS requests,
                       COALESCE(SUM(cost_usd), 0) AS cost_usd,
                       COALESCE(SUM(rated_price_usd), 0) AS rated_usd
                FROM requests {where}
                GROUP BY tenant_id
                ORDER BY rated_usd DESC"""
        ),
        params,
    )
    rows: list[dict[str, Any]] = []
    for row in result:
        cost = float(row[2])
        rated = float(row[3])
        margin = rated - cost
        rows.append(
            {
                "tenant_id": row[0],
                "requests": int(row[1]),
                "cost_usd": cost,
                "rated_usd": rated,
                "margin_usd": margin,
                "margin_pct": _margin_pct(margin, rated),
            }
        )
    return rows


async def get_tenant_line_items(
    session: AsyncSession,
    *,
    tenant: str,
    period: str = "month",
    start_ts: float | None = None,
    end_ts: float | None = None,
    project: str | None = None,
) -> list[dict[str, Any]]:
    """Return one billing line item per (modality, model) for a single tenant.

    This is the invoice-detail breakdown behind a tenant's rolled-up total.
    """
    since, until = resolve_window(period, start_ts, end_ts)
    where, params = _build_where(
        since=since, until=until, project=project, tenant=tenant
    )
    result = await session.execute(
        text(
            f"""SELECT modality, model_id, provider,
                       COUNT(*) AS requests,
                       COALESCE(SUM(input_units), 0) AS input_units,
                       COALESCE(SUM(output_units), 0) AS output_units,
                       COALESCE(SUM(cost_usd), 0) AS cost_usd,
                       COALESCE(SUM(rated_price_usd), 0) AS rated_usd
                FROM requests {where}
                GROUP BY modality, model_id, provider
                ORDER BY rated_usd DESC"""
        ),
        params,
    )
    rows: list[dict[str, Any]] = []
    for row in result:
        cost = float(row[6])
        rated = float(row[7])
        margin = rated - cost
        rows.append(
            {
                "modality": row[0],
                "model_id": row[1],
                "provider": row[2],
                "requests": int(row[3]),
                "input_units": float(row[4]),
                "output_units": float(row[5]),
                "cost_usd": cost,
                "rated_usd": rated,
                "margin_usd": margin,
            }
        )
    return rows


__all__ = ["get_billable_usage", "get_tenant_line_items"]
