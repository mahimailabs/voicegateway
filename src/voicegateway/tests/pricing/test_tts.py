"""Unit tests for voicegateway.inference.pricing.tts (voice-prices backed)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from voicegateway.inference.pricing import tts

# Every cloud TTS model VoiceGateway supports must be priced by voice-prices
# (the Phase 0 coverage gate). Self-hosted local/* models are handled by the
# catalog facade, not here.
_SUPPORTED_CLOUD_TTS = [
    "cartesia/sonic-3",
    "elevenlabs/eleven_turbo_v2_5",
    "deepgram/aura-2",
    "openai/tts-1",
]


def test_pricing_source_format() -> None:
    """PRICING_SOURCE follows the `voice-prices@<version>` shape."""
    assert tts.PRICING_SOURCE.startswith("voice-prices@")
    version = tts.PRICING_SOURCE.split("@", 1)[1]
    assert version, "version segment should be non-empty"


def test_cartesia_priced_correctly() -> None:
    """1000 chars at voice-prices' Cartesia rate = $0.04."""
    assert tts.calculate_tts_cost("cartesia/sonic-3", 1000) == Decimal("0.04")


def test_openai_tts1_priced_correctly() -> None:
    """1000 chars at voice-prices' OpenAI TTS rate = $0.015."""
    assert tts.calculate_tts_cost("openai/tts-1", 1000) == Decimal("0.015")


@pytest.mark.parametrize("model", _SUPPORTED_CLOUD_TTS)
def test_supported_cloud_models_are_priced(model: str) -> None:
    """Every supported cloud TTS model resolves to a positive price."""
    cost = tts.calculate_tts_cost(model, 1000)
    assert cost is not None, f"{model} is not priced by voice-prices"
    assert cost > Decimal("0")


def test_unknown_model_returns_none() -> None:
    """Unknown model returns None (no silent zero contract)."""
    assert tts.calculate_tts_cost("foo/bar-baz", 1000) is None


def test_zero_chars_returns_zero_decimal() -> None:
    """Zero usage returns Decimal('0'), not None."""
    cost = tts.calculate_tts_cost("cartesia/sonic-3", 0)
    assert cost is not None
    assert cost == Decimal("0")


def test_character_count_scales_linearly() -> None:
    """100 chars is one tenth of 1000 chars: $0.04 / 10 = $0.004."""
    assert tts.calculate_tts_cost("cartesia/sonic-3", 100) == Decimal("0.004")


def test_negative_chars_raises() -> None:
    """A negative character count is a programming error, not a $0 request."""
    with pytest.raises(ValueError):
        tts.calculate_tts_cost("cartesia/sonic-3", -1)


def test_return_type_is_decimal() -> None:
    cost = tts.calculate_tts_cost("cartesia/sonic-3", 1000)
    assert isinstance(cost, Decimal)
