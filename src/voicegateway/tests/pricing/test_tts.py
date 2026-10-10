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


@pytest.mark.parametrize(
    "model,chars,expected",
    [
        ("cartesia/sonic-3", 1000, "0.05"),
        ("cartesia/sonic-3", 100, "0.005"),
        ("cartesia/sonic-3", 0, "0"),
        ("openai/tts-1", 1000, "0.015"),
    ],
)
def test_tts_cost_is_exact_decimal(model: str, chars: int, expected: str) -> None:
    cost = tts.calculate_tts_cost(model, chars)
    assert isinstance(cost, Decimal)
    assert cost == Decimal(expected)


@pytest.mark.parametrize("model", _SUPPORTED_CLOUD_TTS)
def test_supported_cloud_models_are_priced(model: str) -> None:
    cost = tts.calculate_tts_cost(model, 1000)
    assert cost is not None, f"{model} is not priced by voice-prices"
    assert cost > Decimal("0")


def test_unknown_model_returns_none() -> None:
    assert tts.calculate_tts_cost("foo/bar-baz", 1000) is None


def test_negative_chars_raises() -> None:
    """A negative character count is a programming error, not a $0 request."""
    with pytest.raises(ValueError):
        tts.calculate_tts_cost("cartesia/sonic-3", -1)
