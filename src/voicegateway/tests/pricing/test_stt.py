"""Unit tests for voicegateway.inference.pricing.stt (voice-prices backed)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from voicegateway.inference.pricing import stt

# Every cloud STT model VoiceGateway supports must be priced by voice-prices
# (the Phase 0 coverage gate). A regression here means voice-prices dropped a
# model or renamed an id; self-hosted local/* models are handled by the
# catalog facade, not here.
_SUPPORTED_CLOUD_STT = [
    "deepgram/nova-3",
    "deepgram/nova-2",
    "deepgram/flux-general",
    "assemblyai/universal-2",
    "openai/whisper-1",
    "groq/whisper-large-v3",
]


def test_pricing_source_format() -> None:
    """PRICING_SOURCE follows the `voice-prices@<version>` shape."""
    assert stt.PRICING_SOURCE.startswith("voice-prices@")
    version = stt.PRICING_SOURCE.split("@", 1)[1]
    assert version, "version segment should be non-empty"


def test_deepgram_nova_3_priced_correctly() -> None:
    """60 audio-seconds = 1 minute. voice-prices nova-3 = $0.0048/min."""
    assert stt.calculate_stt_cost("deepgram/nova-3", 60) == Decimal("0.0048")


@pytest.mark.parametrize("model", _SUPPORTED_CLOUD_STT)
def test_supported_cloud_models_are_priced(model: str) -> None:
    """Every supported cloud STT model resolves to a positive price."""
    cost = stt.calculate_stt_cost(model, 60)
    assert cost is not None, f"{model} is not priced by voice-prices"
    assert cost > Decimal("0")


def test_unknown_model_returns_none() -> None:
    """Unknown model returns None (no silent zero contract)."""
    assert stt.calculate_stt_cost("foo/bar-baz", 60) is None


def test_zero_seconds_returns_zero_decimal() -> None:
    """Zero usage returns Decimal('0'), not None."""
    cost = stt.calculate_stt_cost("deepgram/nova-3", 0)
    assert cost is not None
    assert cost == Decimal("0")


def test_fractional_minute() -> None:
    """30 seconds = half a minute: 0.5 x $0.0048 = $0.0024."""
    assert stt.calculate_stt_cost("deepgram/nova-3", 30) == Decimal("0.0024")


def test_one_hour() -> None:
    """3600 seconds = 60 min x $0.0048 = $0.288."""
    assert stt.calculate_stt_cost("deepgram/nova-3", 3600) == Decimal("0.288")


def test_negative_seconds_raises() -> None:
    """A negative duration is a programming error, not a $0 request."""
    with pytest.raises(ValueError):
        stt.calculate_stt_cost("deepgram/nova-3", -1)


def test_return_type_is_decimal() -> None:
    cost = stt.calculate_stt_cost("deepgram/nova-3", 60)
    assert isinstance(cost, Decimal)
