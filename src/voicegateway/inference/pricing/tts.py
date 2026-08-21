"""TTS pricing via voice-prices."""

from __future__ import annotations

from decimal import Decimal

import voice_prices
from voice_prices import Usage

from voicegateway.inference.pricing._calc import price_usage

PRICING_SOURCE = f"voice-prices@{voice_prices.__version__}"


def calculate_tts_cost(model: str, character_count: int) -> Decimal | None:
    """Return total TTS cost in USD, or None if the model is unknown.

    Self-hosted ``local/*`` models are intercepted as free upstream in the
    catalog facade and never reach here.
    """
    return calculate_tts_cost_detail(model, character_count)[0]


def calculate_tts_cost_detail(
    model: str, character_count: int
) -> tuple[Decimal | None, tuple[str, ...]]:
    """As :func:`calculate_tts_cost`, plus the units that carry no rate."""
    if character_count < 0:
        raise ValueError(f"character_count must be non-negative, got {character_count}")
    usage = Usage(characters=character_count)
    return price_usage(usage, model)
