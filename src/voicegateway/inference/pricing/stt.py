"""STT pricing via voice-prices."""

from __future__ import annotations

from decimal import Decimal

import voice_prices
from voice_prices import Usage, calc_price

PRICING_SOURCE = f"voice-prices@{voice_prices.__version__}"


def calculate_stt_cost(model: str, audio_seconds: float) -> Decimal | None:
    """Return total STT cost in USD, or None if the model is unknown.

    Self-hosted ``local/*`` models are intercepted as free upstream in the
    catalog facade and never reach here.
    """
    if audio_seconds < 0:
        raise ValueError(f"audio_seconds must be non-negative, got {audio_seconds}")
    if "/" in model:
        provider, _, ref = model.partition("/")
    else:
        provider, ref = "", model

    usage = Usage(audio_input_seconds=Decimal(str(audio_seconds)))
    try:
        price = calc_price(usage, model_ref=ref, provider_id=provider or None)
    except LookupError:
        return None
    if price is None:
        return None

    return Decimal(str(price.total_price))
