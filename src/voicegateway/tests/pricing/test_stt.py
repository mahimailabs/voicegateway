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


@pytest.mark.parametrize(
    "seconds,expected",
    [(60, "0.0048"), (30, "0.0024"), (3600, "0.288"), (0, "0")],
)
def test_deepgram_nova_3_is_exact_decimal(seconds: int, expected: str) -> None:
    """nova-3 = $0.0048/min, prorated per second, zero is $0 not None."""
    cost = stt.calculate_stt_cost("deepgram/nova-3", seconds)
    assert isinstance(cost, Decimal)
    assert cost == Decimal(expected)


@pytest.mark.parametrize("model", _SUPPORTED_CLOUD_STT)
def test_supported_cloud_models_are_priced(model: str) -> None:
    cost = stt.calculate_stt_cost(model, 60)
    assert cost is not None, f"{model} is not priced by voice-prices"
    assert cost > Decimal("0")


def test_unknown_model_returns_none() -> None:
    assert stt.calculate_stt_cost("foo/bar-baz", 60) is None


def test_negative_seconds_raises() -> None:
    """A negative duration is a programming error, not a $0 request."""
    with pytest.raises(ValueError):
        stt.calculate_stt_cost("deepgram/nova-3", -1)
