"""Billing endpoints: GET /v1/billing/usage + GET /v1/billing/rate-card.

The rating layer's read surface. ShipVoice (or any biller) polls
``/usage`` for rated revenue + margin per tenant over a window, and reads
``/rate-card`` to see the price book in effect. Rate-card edits (PUT) land
in a follow-up once the DB-override store ships; today the card is the
YAML seed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Query, Request

from voicegateway.billing.rate_card import RateCard
from voicegateway.clickhouse import read_repository as ch_read
from voicegateway.repository.cost_repository import resolve_window
from voicegateway.server.api._deps import get_gateway
from voicegateway.server.api._helpers import parse_iso_date

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

router = APIRouter(prefix="/billing", tags=["billing"])


def _totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Sum a set of per-tenant rollup rows into a grand total."""
    cost = sum(r["cost_usd"] for r in rows)
    rated = sum(r["rated_usd"] for r in rows)
    return {
        "requests": sum(r["requests"] for r in rows),
        "cost_usd": cost,
        "rated_usd": rated,
        "margin_usd": rated - cost,
    }


@router.get("/usage")
async def billing_usage(
    request: Request,
    period: str | None = Query(None),
    project: str | None = Query(None),
    tenant: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
    gateway: Gateway = Depends(get_gateway),
) -> dict[str, Any]:
    """Rated revenue, cost, and margin per tenant for the window.

    When ``tenant`` is given, the response also carries that tenant's
    per-(modality, model) line items for invoice detail. A ClickHouse-backed
    collector reads from ClickHouse; otherwise the SQL store is used. Both
    return the same row shape.
    """
    effective_period = period if period is not None else "month"
    start_ts = parse_iso_date(start, end_of_day=False) if start else None
    end_ts = parse_iso_date(end, end_of_day=True) if end else None

    base: dict[str, Any] = {
        "period": effective_period,
        "start": start,
        "end": end,
        "tenant": tenant,
    }
    if gateway.storage is None:
        return {**base, "tenants": [], "totals": _totals([]), "line_items": []}

    ch_client = getattr(request.app.state, "ch_client", None)
    if ch_client is not None:
        since, until = resolve_window(effective_period, start_ts, end_ts)
        rows = await ch_read.get_billable_usage(
            ch_client, since=since, until=until, tenant=tenant, project=project
        )
        result = {**base, "tenants": rows, "totals": _totals(rows)}
        if tenant is not None:
            result["line_items"] = await ch_read.get_tenant_line_items(
                ch_client, tenant=tenant, since=since, until=until, project=project
            )
        return result

    rows = await gateway.storage.get_billable_usage(
        period=effective_period,
        start_ts=start_ts,
        end_ts=end_ts,
        project=project,
        tenant=tenant,
    )
    result = {**base, "tenants": rows, "totals": _totals(rows)}
    if tenant is not None:
        result["line_items"] = await gateway.storage.get_tenant_line_items(
            tenant,
            period=effective_period,
            start_ts=start_ts,
            end_ts=end_ts,
            project=project,
        )
    return result


def _serialize_rule(rule: Any) -> dict[str, Any]:
    return {
        "modality": rule.modality,
        "provider": rule.provider,
        "model": rule.model,
        "tenant": rule.tenant,
        "plan": rule.plan,
        "kind": rule.kind,
        "markup": rule.markup,
        "unit_price_usd": rule.unit_price_usd,
        "unit": rule.unit,
        "rule": rule.describe(),
    }


@router.get("/rate-card")
async def get_rate_card(
    gateway: Gateway = Depends(get_gateway),
) -> dict[str, Any]:
    """Return the rate card in effect (default markup + rules)."""
    card = RateCard.from_config(gateway.config.rate_card)
    return {
        "default_markup": card.default_markup,
        "rules": [_serialize_rule(r) for r in card.rules],
    }
