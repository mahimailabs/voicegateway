"""ORM model for the ``managed_rate_rules`` table.

DB-side rate-card overrides layered on top of the YAML ``rate_card:`` seed.
One row per scope: the primary key ``rule_id`` is a deterministic key built
from (tenant, plan, modality, provider, model), so ``prices set`` for the same
scope updates the existing row rather than accumulating duplicates.

The gateway merges these rows after the seed rules when building the effective
:class:`voicegateway.billing.rate_card.RateCard`, so a DB override wins a
specificity tie against a seed rule at the same scope.
"""

from __future__ import annotations

from typing import ClassVar

from sqlmodel import Field, SQLModel


class ManagedRateRule(SQLModel, table=True):
    """A persisted rate-card rule (DB override)."""

    __tablename__: ClassVar[str] = "managed_rate_rules"

    rule_id: str = Field(primary_key=True)
    modality: str = Field(default="*", sa_column_kwargs={"server_default": "*"})
    provider: str = Field(default="*", sa_column_kwargs={"server_default": "*"})
    model: str = Field(default="*", sa_column_kwargs={"server_default": "*"})
    tenant: str | None = None
    plan: str | None = None
    kind: str = Field(
        default="cost_plus", sa_column_kwargs={"server_default": "cost_plus"}
    )
    markup: float | None = None
    unit_price_usd: float | None = None
    unit: str | None = None
    created_at: float
    updated_at: float
