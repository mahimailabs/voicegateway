"""Rating arithmetic: resolved rule + recorded cost/units -> billable price.

Kept separate from :mod:`voicegateway.billing.rate_card` (data + resolution)
so the arithmetic can be read and tested on its own. The one entry point the
write path uses is :func:`price`; it resolves against a card and falls back to
the card's ``default_markup`` when no rule matches.

TWO THINGS HERE ARE EASY TO GET WRONG, so both are stated once:

**Cached prompt tokens are a SUBSET of input tokens, not a sibling.** LiveKit
reports ``LLMMetrics.prompt_cached_tokens`` as part of ``prompt_tokens``, and
``inference/pricing/llm.py`` documents the same contract for the catalogue
path. So the uncached leg is ``input - cached``. Billing ``input`` and
``cached`` as two separate quantities double-charges every cached token, which
on an agent with a long stable system prompt is most of the prompt.

**A unit belongs to a modality.** ``input_units`` holds minutes for stt,
characters for tts and prompt tokens for llm, so a ``minute`` rule meeting a
tts request multiplies a character count by a per-minute rate and reports the
result as money. :func:`billable_quantity` rejects the mismatch rather than
returning a plausible number.
"""

from __future__ import annotations

from dataclasses import dataclass

from voicegateway.billing.rate_card import (
    TOKEN_UNITS,
    RateCard,
    RateRule,
    _fmt,
    unit_matches_modality,
)

# How many of a unit's base measure make up one billable unit. STT records
# minutes in ``input_units``; TTS records characters in ``input_units``; LLM
# records prompt tokens in ``input_units`` and completion tokens in
# ``output_units``.
_TOKEN_DIVISOR = {"token": 1.0, "1k_token": 1_000.0, "1m_token": 1_000_000.0}


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

    Single-sided units only. Token units raise, because their price is three
    rates over three quantities and collapsing that to one number is the bug
    this function used to have: it returned ``input + output`` and charged
    both legs at whichever rate the operator happened to type.

    Raises ``ValueError`` for an unrecognized unit, and for a unit that does
    not belong to ``modality``.
    """
    if unit in TOKEN_UNITS:
        raise ValueError(
            f"unit {unit!r} prices input and output separately; use "
            f"token_leg_price() with a rule carrying input/output rates"
        )
    if not unit_matches_modality(unit, modality):
        raise ValueError(f"unit {unit!r} is not billable for modality {modality!r}")
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
    raise ValueError(f"unknown billable unit: {unit!r}")


def token_leg_price(
    rule: RateRule,
    *,
    input_units: float,
    output_units: float,
    cached_input_units: float = 0.0,
) -> float:
    """Price an LLM request from a rule's three per-token legs.

    ``cached_input_units`` is the cached SUBSET of ``input_units``, so the
    uncached leg is the difference. The clamp mirrors
    ``inference/pricing/llm.py``: a malformed metric reporting more cached
    than prompt tokens must not produce a negative uncached leg.
    """
    divisor = _TOKEN_DIVISOR[str(rule.unit)]
    cached = max(0.0, min(cached_input_units, input_units))
    uncached = input_units - cached
    total = (
        uncached * (rule.input_price_usd or 0.0)
        + cached * rule.cached_rate()
        + output_units * (rule.output_price_usd or 0.0)
    )
    return total / divisor


def apply_rule(
    rule: RateRule,
    *,
    cost_usd: float,
    modality: str,
    input_units: float = 0.0,
    output_units: float = 0.0,
    cached_input_units: float = 0.0,
) -> RatedResult:
    """Apply a resolved rule to a request's recorded cost and units."""
    if rule.kind == "fixed":
        if rule.unit in TOKEN_UNITS:
            rated = token_leg_price(
                rule,
                input_units=input_units,
                output_units=output_units,
                cached_input_units=cached_input_units,
            )
        else:
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
    cached_input_units: float = 0.0,
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
        cached_input_units=cached_input_units,
    )


__all__ = [
    "RatedResult",
    "apply_rule",
    "billable_quantity",
    "price",
    "token_leg_price",
]
