"""Billing endpoints under /v1/billing.

The rating layer's HTTP surface. ShipVoice (or any biller) polls ``/usage``
for rated revenue + margin per tenant over a window, reads ``/rate-card`` to
see the price book in effect, and edits the DB overrides through
``/rate-card/rules`` (the same store ``voicegw prices set`` writes to).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from voicegateway.billing.rate_card import RateCard
from voicegateway.clickhouse import read_repository as ch_read
from voicegateway.inference.pricing.catalog import calculate_cost
from voicegateway.inference.pricing.catalog import (
    pricing_source as catalog_pricing_source,
)
from voicegateway.repository import request_log_repository
from voicegateway.repository.cost_repository import resolve_window
from voicegateway.server.api._deps import get_gateway, require_scope
from voicegateway.server.api._helpers import parse_iso_date

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

router = APIRouter(prefix="/billing", tags=["billing"])
write_dep = Depends(require_scope("write"))


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


async def _effective_card(gateway: Gateway) -> RateCard:
    """The card in effect: YAML seed rules plus DB overrides."""
    seed = RateCard.from_config(gateway.config.rate_card)
    if gateway.storage is None:
        return seed
    rows = await gateway.storage.list_rate_rules()
    return seed.with_overrides(rows)


@router.get("/rate-card")
async def get_rate_card(
    gateway: Gateway = Depends(get_gateway),
) -> dict[str, Any]:
    """Return the rate card in effect (default markup + seed + DB rules)."""
    card = await _effective_card(gateway)
    return {
        "default_markup": card.default_markup,
        "rules": [_serialize_rule(r) for r in card.rules],
    }


@router.get("/rate-card/rules")
async def list_rate_card_rules(
    gateway: Gateway = Depends(get_gateway),
) -> dict[str, Any]:
    """Return the editable DB override rules (each with its ``rule_id``)."""
    if gateway.storage is None:
        return {"rules": []}
    return {"rules": await gateway.storage.list_rate_rules()}


@router.post("/rate-card/rules", dependencies=[write_dep])
async def upsert_rate_card_rule(
    body: dict[str, Any],
    gateway: Gateway = Depends(get_gateway),
) -> dict[str, Any]:
    """Upsert a DB rate-card override for a scope (one rule per scope).

    Body: ``{modality?, provider?, model?, tenant?, plan?}`` scope plus either
    ``markup`` (cost-plus) or ``fixed`` + ``unit``. Takes effect on the next
    config refresh, which this triggers.
    """
    if gateway.storage is None:
        raise HTTPException(400, "Storage not enabled")
    try:
        rule_id = await gateway.storage.upsert_rate_rule(
            modality=body.get("modality", "*"),
            provider=body.get("provider", "*"),
            model=body.get("model", "*"),
            tenant=body.get("tenant"),
            plan=body.get("plan"),
            markup=body.get("markup"),
            fixed=body.get("fixed"),
            unit=body.get("unit"),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    await gateway.storage.log_audit_event("rate_rule", rule_id, "set", body, "api")
    await gateway.refresh_config()
    return {"rule_id": rule_id, "created": True}


@router.delete("/rate-card/rules/{rule_id}", dependencies=[write_dep])
async def delete_rate_card_rule(
    rule_id: str,
    gateway: Gateway = Depends(get_gateway),
) -> dict[str, Any]:
    """Delete a DB override by ``rule_id`` (from ``GET /rate-card/rules``)."""
    if gateway.storage is None:
        raise HTTPException(400, "Storage not enabled")
    removed = await gateway.storage.delete_rate_rule(rule_id)
    if not removed:
        raise HTTPException(404, f"no rate rule with id {rule_id!r}")
    await gateway.storage.log_audit_event("rate_rule", rule_id, "delete", None, "api")
    await gateway.refresh_config()
    return {"rule_id": rule_id, "deleted": True}


# Canonical unit and cost-calculation kwargs per modality for voice-prices display.
# Each entry maps modality -> (unit_label, kwargs_for_one_unit_of_that_measure).
_UNIT_QTY: dict[str, tuple[str, dict[str, Any]]] = {
    "stt": ("minute", {"audio_seconds": 60.0}),
    "tts": ("1k_char", {"character_count": 1000}),
    "llm": ("1m_token", {"input_tokens": 1_000_000}),
}


def _voice_price(modality: str, model: str) -> float | None:
    """The voice-prices unit cost for one canonical unit, or None.

    KEPT AS THE INPUT LEG for llm, unchanged, because it is published and a
    caller reading it today would have a number move underneath them. See
    :func:`_voice_prices` for the pair, which is what a UI should render.
    """
    spec = _UNIT_QTY.get(modality)
    if spec is None:
        return None
    cost: Decimal | None = calculate_cost(modality, model, **spec[1])
    return float(cost) if cost is not None else None


def _voice_prices(modality: str, model: str) -> dict[str, Any]:
    """Unit prices for a model, split by leg where the modality has two.

    LLM PRICING IS TWO NUMBERS AND WAS BEING SHOWN AS ONE. ``_voice_price``
    prices 1,000,000 INPUT tokens and labels the result ``1m_token``, which
    reads as covering both legs. Measured against voice-prices 0.3.0, output is
    4.0x input on ``openai/gpt-4o-mini``, 2.5x on ``anthropic/claude-sonnet-4-5``
    and 1.3x on ``groq/llama-3.3-70b-versatile``. A conversational agent is
    output-heavy, so a UI rendering the single number understates the cost of
    exactly the choice the field exists to inform.

    STT and TTS are genuinely one-sided (audio in, characters in), so they
    report a single ``unit_price_usd`` and no legs.

    ``priced`` is explicit rather than inferred from a null, because a null
    could not be told apart from "this endpoint does not handle this modality".
    """
    spec = _UNIT_QTY.get(modality)
    if spec is None:
        return {"priced": False, "unit": None, "reason": "unknown modality"}
    unit = spec[0]
    if modality == "llm":
        inp = calculate_cost(modality, model, input_tokens=1_000_000)
        out = calculate_cost(modality, model, output_tokens=1_000_000)
        if inp is None and out is None:
            return {
                "priced": False,
                "unit": unit,
                "reason": "not in the voice-prices catalog",
            }
        return {
            "priced": True,
            "unit": unit,
            "input_price_usd": float(inp) if inp is not None else None,
            "output_price_usd": float(out) if out is not None else None,
        }
    cost = calculate_cost(modality, model, **spec[1])
    if cost is None:
        return {
            "priced": False,
            "unit": unit,
            "reason": "not in the voice-prices catalog",
        }
    return {"priced": True, "unit": unit, "unit_price_usd": float(cost)}


def _split_ref(model: str) -> tuple[str, str]:
    """``(provider, model)`` from a ``provider/model`` ref, or ``("", model)``."""
    provider, sep, _rest = model.partition("/")
    return (provider, model) if sep else ("", model)


@router.get("/rate-card/quote")
async def rate_card_quote(
    modality: str = Query(..., description="stt | llm | tts"),
    model: str = Query(..., description="provider/model, e.g. openai/gpt-4o-mini"),
    tenant: str | None = Query(None),
    plan: str | None = Query(None),
    gateway: Gateway = Depends(get_gateway),
) -> dict[str, Any]:
    """Price a model WITHOUT it having run yet.

    ``/rate-card/models`` answers only for models already in telemetry, because
    it is fed by ``SELECT DISTINCT ... FROM requests``. An agent-definition
    editor picks a model before a single call exists, so it had nothing to ask.
    This route reads the catalogue and the override store directly.

    Returns the CATALOGUE price and, separately, the override that would apply.
    An editor should show ``effective`` when it is present, because that is what
    the operator will actually be charged; the catalogue price is what the
    override replaced and is kept so the two can be compared.

    ``priced`` is explicit. A bare null could not be told apart from "this
    endpoint does not handle this modality", and those want different UI.
    """
    provider, _ = _split_ref(model)
    price = _voice_prices(modality, model)
    rule = None
    if gateway.storage is not None:
        card = await _effective_card(gateway)
        rule = card.resolve(
            modality=modality,
            provider=provider,
            model_id=model,
            tenant=tenant,
            plan=plan,
        )
    return {
        "modality": modality,
        "provider": provider,
        "model": model,
        "pricing_source": catalog_pricing_source(modality),
        "catalog": price,
        "effective": _serialize_rule(rule) if rule is not None else None,
    }


@router.post("/rate-card/quote")
async def rate_card_quote_bulk(
    body: dict[str, Any],
    gateway: Gateway = Depends(get_gateway),
) -> dict[str, Any]:
    """Quote several models at once: ``{"models": [{modality, model}, ...]}``.

    One request per dropdown entry is the shape an editor would otherwise be
    forced into, and the card is resolved once here rather than per model.
    """
    items = body.get("models")
    if not isinstance(items, list):
        raise HTTPException(400, 'body must be {"models": [{modality, model}, ...]}')
    if len(items) > 200:
        raise HTTPException(413, f"too many models: {len(items)} exceeds 200")
    card = await _effective_card(gateway) if gateway.storage is not None else None
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        modality = str(item.get("modality") or "")
        model = str(item.get("model") or "")
        provider, _ = _split_ref(model)
        rule = (
            card.resolve(
                modality=modality,
                provider=provider,
                model_id=model,
                tenant=item.get("tenant"),
                plan=item.get("plan"),
            )
            if card is not None
            else None
        )
        out.append(
            {
                "modality": modality,
                "provider": provider,
                "model": model,
                "pricing_source": catalog_pricing_source(modality),
                "catalog": _voice_prices(modality, model),
                "effective": _serialize_rule(rule) if rule is not None else None,
            }
        )
    return {"models": out}


@router.get("/rate-card/models")
async def rate_card_models(
    gateway: Gateway = Depends(get_gateway),
) -> dict[str, Any]:
    """Distinct models seen in telemetry, each with voice-prices unit cost and effective rule.

    Lets the operator spot stale prices and see which rate rule applies per model.
    ``voice_price_usd`` is null when the model is not in the voice-prices catalog.
    ``effective`` is the matching rate rule, or null when no explicit rule matches
    (the default markup applies).
    """
    if gateway.storage is None:
        return {"models": []}
    await gateway.storage._ensure_initialized()
    card = await _effective_card(gateway)
    async with gateway.storage._conn.session() as db:
        used = await request_log_repository.read_models_in_use(db)
    out: list[dict[str, Any]] = []
    for m in used:
        rule = card.resolve(
            modality=m["modality"],
            provider=m["provider"],
            model_id=m["model"],
            tenant=None,
            plan=None,
        )
        unit_label: str | None = _UNIT_QTY.get(m["modality"], (None, {}))[0]
        out.append(
            {
                "modality": m["modality"],
                "provider": m["provider"],
                "model": m["model"],
                "unit": unit_label,
                # Kept for compatibility. It is the INPUT leg for llm.
                "voice_price_usd": _voice_price(m["modality"], m["model"]),
                # What a UI should render: split by leg, with `priced` explicit.
                "price": _voice_prices(m["modality"], m["model"]),
                "effective": _serialize_rule(rule) if rule is not None else None,
            }
        )
    return {"models": out}
