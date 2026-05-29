"""TTS pricing via voice-prices."""

from __future__ import annotations

from decimal import Decimal

import voice_prices
from voice_prices import Usage, calc_price

PRICING_SOURCE = f"voice-prices@{voice_prices.__version__}"


def calculate_tts_cost(model: str, character_count: int) -> Decimal | None:
    """Return total TTS cost in USD, or None if the model is unknown.

    Self-hosted ``local/*`` models are intercepted as free upstream in the
    catalog facade and never reach here.
    """
    if character_count < 0:
        raise ValueError(f"character_count must be non-negative, got {character_count}")
    if "/" in model:
        provider, _, ref = model.partition("/")
    else:
        provider, ref = "", model

    usage = Usage(characters=character_count)
    try:
        price = calc_price(usage, model_ref=ref, provider_id=provider or None)
    except LookupError:
        return None
    if price is None:
        return None

    return Decimal(str(price.total_price))
