"""Input schemas for the rate-card MCP tool group."""

from __future__ import annotations

from voicegateway.schemas.mcp import StrictMcpInput


class GetRateCardInput(StrictMcpInput):
    """Input for get_rate_card (no arguments)."""


class SetRateCardOverrideInput(StrictMcpInput):
    """Input for set_rate_card_override: a scope plus a markup or fixed price.

    The scope defaults to the wildcard ``*`` for modality/provider/model, so an
    unscoped call sets the default. Provide either ``markup`` (a cost-plus
    multiplier) or ``fixed`` + ``unit`` (a fixed per-unit price).
    """

    modality: str = "*"
    provider: str = "*"
    model: str = "*"
    tenant: str | None = None
    plan: str | None = None
    markup: float | None = None
    fixed: float | None = None
    unit: str | None = None


class DeleteRateCardOverrideInput(StrictMcpInput):
    """Input for delete_rate_card_override: the rule_id to remove."""

    rule_id: str
