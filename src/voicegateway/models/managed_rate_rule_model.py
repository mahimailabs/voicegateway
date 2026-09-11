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
    # Which ledger side the rule sets: "price" (what the tenant is charged,
    # the historical behaviour and the default) or "cost" (what the operator
    # pays, replacing the catalogue figure).
    sets: str = Field(default="price", sa_column_kwargs={"server_default": "price"})
    kind: str = Field(
        default="cost_plus", sa_column_kwargs={"server_default": "cost_plus"}
    )
    markup: float | None = None
    unit_price_usd: float | None = None
    unit: str | None = None
    # LLM legs. A token unit bills input and output at different rates, so it
    # carries a rate per leg instead of a single ``unit_price_usd``.
    input_price_usd: float | None = None
    cached_input_price_usd: float | None = None
    output_price_usd: float | None = None
    # FX provenance. NULL on every rule entered directly in USD, which is the
    # honest reading: no rate was involved. Populated only by
    # ``voicegw prices set --from``, which converts once at set time and stores
    # the result as a fixed figure, so the price never floats afterwards.
    #
    # Text rather than float for the published amount and the rate: this is
    # evidence, and it has to still match the vendor page it was read from.
    source_currency: str | None = None
    source_amount: str | None = None
    fx_rate: str | None = None
    fx_source: str | None = None
    #: When VG fetched the rate, not when the provider published it. The
    #: staleness clock starts at the thing we can attest to.
    fx_fetched_at: float | None = None
    created_at: float
    updated_at: float
