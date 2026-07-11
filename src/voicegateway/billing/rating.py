"""Rating arithmetic: resolved rule + recorded cost/units -> billable price.

Kept separate from :mod:`voicegateway.billing.rate_card` (data + resolution)
so the arithmetic can be read and tested on its own. The one entry point the
write path uses is :func:`price`; it resolves against a card and falls back to
the card's ``default_markup`` when no rule matches.
"""

from __future__ import annotations

from dataclasses import dataclass

from voicegateway.billing.rate_card import RateCard, RateRule, _fmt

# How each fixed-price unit maps onto a request's recorded units. STT records
# minutes in ``input_units``; TTS records characters in ``input_units``; LLM
# records prompt tokens in ``input_units`` and completion tokens in
# ``output_units``. ``request`` is a flat per-call price.
_TOKEN_UNITS = {"token", "1k_token", "1m_token"}


@dataclass(frozen=True)
class RatedResult:
    """The billable price for one request plus the rule that produced it."""

    rated_price_usd: float
    rate_rule: str


def billable_quantity(
    unit: str,
    *,
    modality: str,
    input_units: float,
    output_units: float,
) -> float:
    """Return the quantity a fixed ``unit_price_usd`` is multiplied by.

    Raises ``ValueError`` for an unrecognized unit (a rejected config
    should never reach here, but the guard keeps the arithmetic honest).
    """
    if unit == "request":
        return 1.0
    if unit == "minute":
        return input_units
    if unit == "second":
        return input_units * 60.0
    if unit == "char":
        return input_units
    if unit == "1k_char":
        return input_units / 1000.0
    if unit in _TOKEN_UNITS:
        tokens = input_units + output_units
        if unit == "token":
            return tokens
        if unit == "1k_token":
            return tokens / 1000.0
        return tokens / 1_000_000.0  # 1m_token
    raise ValueError(f"unknown billable unit: {unit!r}")


def apply_rule(
    rule: RateRule,
    *,
    cost_usd: float,
    modality: str,
    input_units: float = 0.0,
    output_units: float = 0.0,
) -> RatedResult:
    """Apply a resolved rule to a request's recorded cost and units."""
    if rule.kind == "fixed":
        qty = billable_quantity(
            str(rule.unit),
            modality=modality,
            input_units=input_units,
            output_units=output_units,
        )
        rated = (rule.unit_price_usd or 0.0) * qty
        return RatedResult(rated_price_usd=rated, rate_rule=rule.describe())
    # cost_plus (default)
    markup = rule.markup if rule.markup is not None else 1.0
    return RatedResult(rated_price_usd=cost_usd * markup, rate_rule=rule.describe())


def price(
    card: RateCard,
    *,
    modality: str,
    provider: str,
    model_id: str,
    cost_usd: float,
    input_units: float = 0.0,
    output_units: float = 0.0,
    tenant: str | None = None,
    plan: str | None = None,
) -> RatedResult:
    """Resolve ``card`` for the request and rate it.

    When no rule matches, fall back to a cost-plus at the card's
    ``default_markup`` and tag the result ``default:<markup>``.
    """
    rule = card.resolve(
        modality=modality,
        provider=provider,
        model_id=model_id,
        tenant=tenant,
        plan=plan,
    )
    if rule is None:
        return RatedResult(
            rated_price_usd=cost_usd * card.default_markup,
            rate_rule=f"default:{_fmt(card.default_markup)}",
        )
    return apply_rule(
        rule,
        cost_usd=cost_usd,
        modality=modality,
        input_units=input_units,
        output_units=output_units,
    )


__all__ = ["RatedResult", "apply_rule", "billable_quantity", "price"]
