"""STT pricing via voice-prices."""

from __future__ import annotations

from decimal import Decimal

import voice_prices
from voice_prices import Usage

from voicegateway.inference.pricing._calc import price_usage

PRICING_SOURCE = f"voice-prices@{voice_prices.__version__}"


def calculate_stt_cost(model: str, audio_seconds: float) -> Decimal | None:
    """Return total STT cost in USD, or None if the model is unknown.

    Self-hosted ``local/*`` models are intercepted as free upstream in the
    catalog facade and never reach here.
    """
    return calculate_stt_cost_detail(model, audio_seconds)[0]


def calculate_stt_cost_detail(
    model: str, audio_seconds: float
) -> tuple[Decimal | None, tuple[str, ...]]:
    """As :func:`calculate_stt_cost`, plus the units that carry no rate.

    See :func:`voicegateway.inference.pricing._calc.price_usage`: a non-empty
    second element means the total is not fully rate-backed.
    """
    if audio_seconds < 0:
        raise ValueError(f"audio_seconds must be non-negative, got {audio_seconds}")
    usage = Usage(audio_input_seconds=Decimal(str(audio_seconds)))
    return price_usage(usage, model)
