"""Async repo for the managed_rate_rules table (DB rate-card overrides).

One row per scope. ``rule_id`` is a deterministic key from
(tenant, plan, modality, provider, model), so an upsert for the same scope
replaces the existing rule rather than accumulating duplicates. The UPSERT is
a ``text()`` clause with ``ON CONFLICT(rule_id) DO UPDATE`` (portable across
SQLite and Postgres).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlmodel import delete, select

from voicegateway.billing.rate_card import VALID_UNITS, WILDCARD
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
) -> str:
    """Deterministic primary key for a rule's scope (one row per scope)."""
    return "|".join(
        [
            tenant or WILDCARD,
            plan or WILDCARD,
            modality,
            provider,
            model,
        ]
    )


def validate_rule(
    *,
    markup: float | None,
    fixed: float | None,
    unit: str | None,
) -> str:
    """Validate a rule's pricing fields and return its kind.

    Exactly one of ``markup`` (cost_plus) or ``fixed`` (fixed $/unit) must be
    set. A fixed rule needs a valid ``unit``. Raises ``ValueError`` otherwise.
    """
    if markup is not None and fixed is not None:
        raise ValueError("a rate rule sets either markup or fixed, not both")
    if fixed is not None:
        if unit not in VALID_UNITS:
            raise ValueError(
                f"a fixed rule needs a valid unit (one of {sorted(VALID_UNITS)})"
            )
        return "fixed"
    if markup is not None:
        if markup <= 0:
            raise ValueError("markup must be > 0")
        return "cost_plus"
    raise ValueError("a rate rule needs either markup or fixed")


_RULE_UPSERT = text(
    """
    INSERT INTO managed_rate_rules (
        rule_id, modality, provider, model, tenant, plan,
        kind, markup, unit_price_usd, unit, created_at, updated_at
    ) VALUES (
        :rule_id, :modality, :provider, :model, :tenant, :plan,
        :kind, :markup, :unit_price_usd, :unit, :now, :now
    )
    ON CONFLICT(rule_id) DO UPDATE SET
        kind=excluded.kind,
        markup=excluded.markup,
        unit_price_usd=excluded.unit_price_usd,
        unit=excluded.unit,
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
        "kind": r.kind,
        "markup": r.markup,
        "unit_price_usd": r.unit_price_usd,
        "unit": r.unit,
        "created_at": r.created_at,
        "updated_at": r.updated_at,
    }


async def list_rules(session: AsyncSession) -> list[dict[str, Any]]:
    """Return every managed_rate_rules row, oldest first."""
    result = await session.execute(
        select(ManagedRateRule).order_by(ManagedRateRule.created_at.asc())  # type: ignore[attr-defined]
    )
    return [_row_to_dict(r) for r in result.scalars().all()]


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
) -> str:
    """Insert or update one rule (keyed by scope). Returns the ``rule_id``."""
    kind = validate_rule(markup=markup, fixed=fixed, unit=unit)
    rid = scope_key(
        modality=modality, provider=provider, model=model, tenant=tenant, plan=plan
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
            "kind": kind,
            "markup": markup if kind == "cost_plus" else None,
            "unit_price_usd": fixed if kind == "fixed" else None,
            "unit": unit if kind == "fixed" else None,
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
    "delete_rule",
    "list_rules",
    "scope_key",
    "upsert_rule",
    "validate_rule",
]
