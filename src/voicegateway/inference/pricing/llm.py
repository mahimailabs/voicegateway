"""LLM pricing via voice-prices."""

from __future__ import annotations

from decimal import Decimal

import voice_prices
from voice_prices import Usage, calc_price

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
    if "/" in model:
        provider, _, ref = model.partition("/")
    else:
        provider, ref = "", model

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
    try:
        price = calc_price(
            usage,
            model_ref=ref,
            provider_id=provider or None,
        )
    except LookupError:
        return None
    if price is None:
        return None

    return Decimal(str(price.total_price))
