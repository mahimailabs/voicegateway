"""Rate-card tools — read the effective card and manage DB overrides.

Lets a coding agent inspect and adjust what VoiceGateway *charges* for a call
(the billable rate on top of the upstream cost) without editing YAML: read the
card in effect, add a per-scope override, and remove one. Thin wrappers over the
same StorageService methods the HTTP ``/v1/billing/rate-card`` routes use, so
overrides take effect on the config refresh each write triggers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from voicegateway.billing.rate_card import RateCard
from voicegateway.core.auth import ADMIN_SCOPE
from voicegateway.schemas.mcp.rate_card_schema import (
    DeleteRateCardOverrideInput,
    GetRateCardInput,
    SetRateCardOverrideInput,
)
from voicegateway.server.mcp.errors import ValidationError
from voicegateway.server.mcp.tools.base import ToolDef, make_tool

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway


def _parse(model_cls: type, arguments: dict[str, Any]) -> Any:
    try:
        return model_cls(**arguments)
    except Exception as exc:
        raise ValidationError(str(exc)) from exc


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


GET_RATE_CARD_DOC = """Return the rate card in effect and its editable overrides.

The effective card is the YAML seed (default markup + seed rules) merged with
the DB overrides. Use ``overrides`` (each carries a ``rule_id``) to see what you
can change with set_rate_card_override / delete_rate_card_override.

Returns:
    default_markup, rules (the effective, merged rules), overrides (the DB rows).
"""

SET_RATE_CARD_OVERRIDE_DOC = """Add or update a DB rate-card override for a scope.

One rule per scope (modality/provider/model, optionally tenant/plan; each
defaults to ``*``). Provide either ``markup`` (cost-plus multiplier, e.g. 1.3)
or ``fixed`` + ``unit`` (a fixed per-unit price). Takes effect on the config
refresh this triggers.

Returns:
    rule_id and created=true.

Raises:
    VALIDATION_ERROR if cost tracking is disabled or the scope/price is invalid.
"""

DELETE_RATE_CARD_OVERRIDE_DOC = """Delete a DB rate-card override by rule_id.

Get the rule_id from get_rate_card's ``overrides``. Takes effect on the config
refresh this triggers.

Returns:
    rule_id and deleted=true.

Raises:
    VALIDATION_ERROR if cost tracking is disabled or no rule has that id.
"""


async def _handle_get_rate_card(
    gateway: Gateway, arguments: dict[str, Any]
) -> dict[str, Any]:
    _parse(GetRateCardInput, arguments)
    seed = RateCard.from_config(gateway.config.rate_card)
    overrides: list[dict[str, Any]] = []
    if gateway.storage is not None:
        overrides = await gateway.storage.list_rate_rules()
        card = seed.with_overrides(overrides)
    else:
        card = seed
    return {
        "default_markup": card.default_markup,
        "rules": [_serialize_rule(r) for r in card.rules],
        "overrides": overrides,
    }


async def _handle_set_rate_card_override(
    gateway: Gateway, arguments: dict[str, Any]
) -> dict[str, Any]:
    payload = _parse(SetRateCardOverrideInput, arguments)
    if gateway.storage is None:
        raise ValidationError(
            "Rate-card overrides require cost_tracking.enabled=true.",
            details={"hint": "enable cost_tracking in voicegw.yaml"},
        )
    try:
        rule_id = await gateway.storage.upsert_rate_rule(
            modality=payload.modality,
            provider=payload.provider,
            model=payload.model,
            tenant=payload.tenant,
            plan=payload.plan,
            markup=payload.markup,
            fixed=payload.fixed,
            unit=payload.unit,
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    await gateway.storage.log_audit_event("rate_rule", rule_id, "set", arguments, "mcp")
    await gateway.refresh_config()
    return {"rule_id": rule_id, "created": True}


async def _handle_delete_rate_card_override(
    gateway: Gateway, arguments: dict[str, Any]
) -> dict[str, Any]:
    payload = _parse(DeleteRateCardOverrideInput, arguments)
    if gateway.storage is None:
        raise ValidationError(
            "Rate-card overrides require cost_tracking.enabled=true.",
            details={"hint": "enable cost_tracking in voicegw.yaml"},
        )
    removed = await gateway.storage.delete_rate_rule(payload.rule_id)
    if not removed:
        raise ValidationError(
            f"No rate rule with id {payload.rule_id!r}.",
            details={"rule_id": payload.rule_id},
        )
    await gateway.storage.log_audit_event(
        "rate_rule", payload.rule_id, "delete", None, "mcp"
    )
    await gateway.refresh_config()
    return {"rule_id": payload.rule_id, "deleted": True}


RATE_CARD_TOOLS: list[ToolDef] = [
    make_tool(
        "get_rate_card", GET_RATE_CARD_DOC, GetRateCardInput, _handle_get_rate_card
    ),
    make_tool(
        "set_rate_card_override",
        SET_RATE_CARD_OVERRIDE_DOC,
        SetRateCardOverrideInput,
        _handle_set_rate_card_override,
    ),
    # Destructive: admin-only.
    make_tool(
        "delete_rate_card_override",
        DELETE_RATE_CARD_OVERRIDE_DOC,
        DeleteRateCardOverrideInput,
        _handle_delete_rate_card_override,
        required_scope=ADMIN_SCOPE,
    ),
]
