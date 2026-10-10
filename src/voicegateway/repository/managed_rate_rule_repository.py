"""Async repo for the managed_rate_rules table (DB rate-card overrides).

One row per scope. ``rule_id`` is a deterministic key from
(tenant, plan, modality, provider, model), so an upsert for the same scope
replaces the existing rule rather than accumulating duplicates. The UPSERT is
a ``text()`` clause with ``ON CONFLICT(rule_id) DO UPDATE`` (portable across
SQLite and Postgres).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlmodel import delete, select

from voicegateway.billing.rate_card import (
    WILDCARD,
    validate_fixed_pricing,
    validate_sets,
)
from voicegateway.models.managed_rate_rule_model import ManagedRateRule

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def scope_key(
    *,
    modality: str,
    provider: str,
    model: str,
    tenant: str | None,
    plan: str | None,
    sets: str = "price",
) -> str:
    """Deterministic primary key for a rule's scope (one row per scope).

    A cost rule and a price rule at the SAME scope are the ordinary
    configuration ("this is what I pay for nova-3, and charge 1.3x on top"),
    so the ledger side has to be part of the identity or the second write
    would overwrite the first.

    Price keys keep the historical format exactly. Only cost rules take the
    prefix, so every row written before ``sets`` existed keeps its id and an
    upsert against it still updates rather than duplicating.
    """
    scope = "|".join(
        [
            tenant or WILDCARD,
            plan or WILDCARD,
            modality,
            provider,
            model,
        ]
    )
    return scope if sets == "price" else f"{sets}|{scope}"


def validate_rule(
    *,
    markup: float | None,
    fixed: float | None,
    unit: str | None,
    modality: str = WILDCARD,
    sets: str = "price",
    input_price_usd: float | None = None,
    cached_input_price_usd: float | None = None,
    output_price_usd: float | None = None,
) -> str:
    """Validate a rule's pricing fields and return its kind.

    Exactly one of ``markup`` (cost_plus) or a fixed price (``fixed`` for a
    single-sided unit, or the input/output legs for a token unit) must be set.
    Fixed-pricing coherence is delegated to
    :func:`voicegateway.billing.rate_card.validate_fixed_pricing` so the API
    and the YAML seed cannot drift apart on what they accept.
    """
    legs = (input_price_usd, cached_input_price_usd, output_price_usd)
    has_fixed = fixed is not None or any(leg is not None for leg in legs)
    validate_sets(sets, "fixed" if has_fixed else "cost_plus")
    if markup is not None and has_fixed:
        raise ValueError("a rate rule sets either markup or a fixed price, not both")
    if has_fixed:
        validate_fixed_pricing(
            modality=modality,
            unit=unit,
            fixed=fixed,
            input_price_usd=input_price_usd,
            cached_input_price_usd=cached_input_price_usd,
            output_price_usd=output_price_usd,
        )
        return "fixed"
    if markup is not None:
        if markup <= 0:
            raise ValueError("markup must be > 0")
        return "cost_plus"
    raise ValueError("a rate rule needs either markup or a fixed price")


@dataclass(frozen=True)
class FxProvenance:
    """Where a converted price came from, stored beside the price.

    Without this a rule set from a foreign-currency figure is
    indistinguishable from a hand-typed one, which makes its staleness
    invisible rather than absent. Amount and rate are strings because they are
    evidence: they have to still match the vendor page they were read from,
    and a float would quietly stop doing that.
    """

    source_currency: str
    source_amount: str
    fx_rate: str
    fx_source: str
    fx_fetched_at: float


_RULE_UPSERT = text(
    """
    INSERT INTO managed_rate_rules (
        rule_id, modality, provider, model, tenant, plan,
        sets, kind, markup, unit_price_usd, unit,
        input_price_usd, cached_input_price_usd, output_price_usd,
        source_currency, source_amount, fx_rate, fx_source, fx_fetched_at,
        created_at, updated_at
    ) VALUES (
        :rule_id, :modality, :provider, :model, :tenant, :plan,
        :sets, :kind, :markup, :unit_price_usd, :unit,
        :input_price_usd, :cached_input_price_usd, :output_price_usd,
        :source_currency, :source_amount, :fx_rate, :fx_source, :fx_fetched_at,
        :now, :now
    )
    ON CONFLICT(rule_id) DO UPDATE SET
        sets=excluded.sets,
        kind=excluded.kind,
        markup=excluded.markup,
        unit_price_usd=excluded.unit_price_usd,
        unit=excluded.unit,
        input_price_usd=excluded.input_price_usd,
        cached_input_price_usd=excluded.cached_input_price_usd,
        output_price_usd=excluded.output_price_usd,
        source_currency=excluded.source_currency,
        source_amount=excluded.source_amount,
        fx_rate=excluded.fx_rate,
        fx_source=excluded.fx_source,
        fx_fetched_at=excluded.fx_fetched_at,
        updated_at=excluded.updated_at
    """
)


def _row_to_dict(r: ManagedRateRule) -> dict[str, Any]:
    """Row shaped like a RateRule (fields map 1:1)."""
    return {
        "rule_id": r.rule_id,
        "modality": r.modality,
        "provider": r.provider,
        "model": r.model,
        "tenant": r.tenant,
        "plan": r.plan,
        "sets": r.sets,
        "kind": r.kind,
        "markup": r.markup,
        "unit_price_usd": r.unit_price_usd,
        "unit": r.unit,
        "input_price_usd": r.input_price_usd,
        "cached_input_price_usd": r.cached_input_price_usd,
        "output_price_usd": r.output_price_usd,
        "created_at": r.created_at,
        "updated_at": r.updated_at,
    }


async def list_rules(session: AsyncSession) -> list[dict[str, Any]]:
    """Return every managed_rate_rules row, oldest first."""
    result = await session.execute(
        select(ManagedRateRule).order_by(ManagedRateRule.created_at.asc())  # type: ignore[attr-defined]
    )
    return [_row_to_dict(r) for r in result.scalars().all()]


async def list_fx_rules(session: AsyncSession) -> list[dict[str, Any]]:
    """Rules whose price was converted from a foreign currency, oldest rate first.

    Deliberately not folded into :func:`list_rules`. That one is shaped 1:1 to
    ``RateRule`` and feeds ``RateCard.with_overrides``; extra keys there would
    reach a constructor that does not expect them. Provenance is an operator
    concern, not a pricing one, so it gets its own read.
    """
    result = await session.execute(
        select(ManagedRateRule)
        .where(ManagedRateRule.fx_fetched_at.is_not(None))  # type: ignore[union-attr]
        .order_by(ManagedRateRule.fx_fetched_at.asc())  # type: ignore[union-attr]
    )
    return [
        {
            "rule_id": r.rule_id,
            "unit": r.unit,
            "unit_price_usd": r.unit_price_usd,
            "source_currency": r.source_currency,
            "source_amount": r.source_amount,
            "fx_rate": r.fx_rate,
            "fx_source": r.fx_source,
            "fx_fetched_at": r.fx_fetched_at,
        }
        for r in result.scalars().all()
    ]


async def upsert_rule(
    session: AsyncSession,
    *,
    modality: str = WILDCARD,
    provider: str = WILDCARD,
    model: str = WILDCARD,
    tenant: str | None = None,
    plan: str | None = None,
    markup: float | None = None,
    fixed: float | None = None,
    unit: str | None = None,
    input_price_usd: float | None = None,
    cached_input_price_usd: float | None = None,
    output_price_usd: float | None = None,
    sets: str = "price",
    fx: FxProvenance | None = None,
) -> str:
    """Insert or update one rule (keyed by scope). Returns the ``rule_id``."""
    kind = validate_rule(
        markup=markup,
        fixed=fixed,
        unit=unit,
        modality=modality,
        sets=sets,
        input_price_usd=input_price_usd,
        cached_input_price_usd=cached_input_price_usd,
        output_price_usd=output_price_usd,
    )
    rid = scope_key(
        modality=modality,
        provider=provider,
        model=model,
        tenant=tenant,
        plan=plan,
        sets=sets,
    )
    await session.execute(
        _RULE_UPSERT,
        {
            "rule_id": rid,
            "modality": modality,
            "provider": provider,
            "model": model,
            "tenant": tenant,
            "plan": plan,
            "sets": sets,
            "kind": kind,
            "markup": markup if kind == "cost_plus" else None,
            "unit_price_usd": fixed if kind == "fixed" else None,
            "unit": unit if kind == "fixed" else None,
            "input_price_usd": input_price_usd if kind == "fixed" else None,
            "cached_input_price_usd": (
                cached_input_price_usd if kind == "fixed" else None
            ),
            "output_price_usd": output_price_usd if kind == "fixed" else None,
            "source_currency": fx.source_currency if fx else None,
            "source_amount": fx.source_amount if fx else None,
            "fx_rate": fx.fx_rate if fx else None,
            "fx_source": fx.fx_source if fx else None,
            "fx_fetched_at": fx.fx_fetched_at if fx else None,
            "now": time.time(),
        },
    )
    await session.commit()
    return rid


async def delete_rule(session: AsyncSession, rule_id: str) -> bool:
    """Delete one rule by ``rule_id``. True when a row was removed."""
    result = await session.execute(
        delete(ManagedRateRule).where(ManagedRateRule.rule_id == rule_id)  # type: ignore[arg-type]
    )
    await session.commit()
    return (result.rowcount or 0) > 0  # type: ignore[attr-defined]


__all__ = [
    "FxProvenance",
    "list_fx_rules",
    "delete_rule",
    "list_rules",
    "scope_key",
    "upsert_rule",
    "validate_rule",
]
