"""LLM pricing via voice-prices."""

from __future__ import annotations

from decimal import Decimal

import voice_prices
from voice_prices import Usage

from voicegateway.inference.pricing._calc import price_usage

PRICING_SOURCE = f"voice-prices@{voice_prices.__version__}"


def calculate_llm_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
) -> Decimal | None:
    """Return total LLM cost in USD, or None if the model is unknown.

    ``cached_input_tokens`` is the subset of ``input_tokens`` served from the
    provider's prompt cache (LK reports as ``LLMMetrics.prompt_cached_tokens``,
    a subset of ``prompt_tokens``). voice-prices' ``cache_read_tokens`` field
    expects the cached portion as a sibling to (non-cached) ``input_tokens``,
    so we clamp it to the prompt total before constructing Usage. Most providers
    discount cached input significantly (OpenAI: 50%, Anthropic: ~10%).
    """
    return calculate_llm_cost_detail(
        model, input_tokens, output_tokens, cached_input_tokens
    )[0]


def calculate_llm_cost_detail(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
) -> tuple[Decimal | None, tuple[str, ...]]:
    """As :func:`calculate_llm_cost`, plus the units that carry no rate.

    See :func:`voicegateway.inference.pricing._calc.price_usage`: a non-empty
    second element means the total is not fully rate-backed.
    """
    # voice-prices expects ``input_tokens`` to carry the TOTAL prompt token
    # count (cached subset included) and computes uncached = input - cached
    # internally. Clamp cached to input to avoid the library's negative-uncached
    # guard from a malformed LK metric.
    cached = max(0, min(int(cached_input_tokens), int(input_tokens)))

    usage = Usage(
        input_tokens=int(input_tokens),
        output_tokens=output_tokens,
        cache_read_tokens=cached or None,
    )
    return price_usage(usage, model)
