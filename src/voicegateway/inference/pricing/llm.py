"""LLM pricing via pydantic/genai-prices."""

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
    """Return total LLM cost in USD, or None if the model is unknown."""
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
        return None
    if price is None:
        return None

    return Decimal(str(price.total_price))
