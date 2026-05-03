"""LLM pricing via pydantic/genai-prices.

This module wraps genai-prices for the LLM modality. STT and TTS
pricing lives in the local catalog (see voicegateway/pricing/stt.py
and voicegateway/pricing/tts.py). The pricing-source attribution
surfaced via PRICING_SOURCE is logged on every request so
reconciliation tooling can verify which upstream catalog produced
the number.
"""

from __future__ import annotations

from decimal import Decimal

import genai_prices
from genai_prices import Usage, calc_price

PRICING_SOURCE = f"genai-prices@{genai_prices.__version__}"


def calculate_llm_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> Decimal | None:
    """Return total LLM cost in USD, or None if the model is unknown.

    Args:
        model: VoiceGateway model ID, e.g. "openai/gpt-4o-mini" or
            "anthropic/claude-3.5-sonnet". The "provider/model" prefix
            is split; both halves are passed to genai-prices for
            matching. A bare model name without a slash is also
            accepted.
        input_tokens: Input token count.
        output_tokens: Output token count.

    Returns:
        Decimal total price in USD when genai-prices recognized the
        model, otherwise None. The function never returns Decimal("0")
        for an unrecognized model: callers must handle the None case
        explicitly rather than silently logging zero cost.
    """
    if "/" in model:
        provider, _, ref = model.partition("/")
    else:
        provider, ref = "", model

    usage = Usage(input_tokens=input_tokens, output_tokens=output_tokens)
    try:
        price = calc_price(
            usage,
            model_ref=ref,
            provider_id=provider or None,
        )
    except LookupError:
        # genai-prices raises LookupError when the provider_id is
        # unknown rather than returning None. Treat both shapes
        # the same: unknown model.
        return None
    if price is None:
        return None
    # genai-prices returns float; Decimal(str(float)) avoids the
    # binary-rounding artifacts that Decimal(float) would carry.
    return Decimal(str(price.total_price))
