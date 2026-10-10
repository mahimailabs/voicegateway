"""Unit tests for voicegateway.inference.pricing.catalog."""

from __future__ import annotations

from decimal import Decimal

import pytest

from voicegateway.inference.pricing import catalog, llm, stt, tts


@pytest.mark.parametrize(
    "modality,model,kwargs,direct",
    [
        (
            "llm",
            "openai/gpt-4o",
            {"input_tokens": 1000, "output_tokens": 100},
            lambda: llm.calculate_llm_cost("openai/gpt-4o", 1000, 100),
        ),
        (
            "stt",
            "deepgram/nova-3",
            {"audio_seconds": 60},
            lambda: stt.calculate_stt_cost("deepgram/nova-3", 60),
        ),
        (
            "tts",
            "cartesia/sonic-3",
            {"character_count": 1000},
            lambda: tts.calculate_tts_cost("cartesia/sonic-3", 1000),
        ),
    ],
)
def test_dispatch_routes_to_modality_module(modality, model, kwargs, direct) -> None:
    """The facade returns exactly what the modality module returns."""
    expected = direct()
    assert expected is not None
    assert catalog.calculate_cost(modality, model, **kwargs) == expected


def test_dispatch_unknown_modality_returns_none() -> None:
    assert catalog.calculate_cost("foo", "bar/baz") is None
    assert catalog.calculate_cost("", "deepgram/nova-3") is None


def test_dispatch_unknown_model_propagates_none() -> None:
    """Known modality + unknown model -> None (no silent zero)."""
    assert catalog.calculate_cost("llm", "foo/bar-baz", input_tokens=100) is None
    assert catalog.calculate_cost("stt", "unknown/model", audio_seconds=60) is None
    assert catalog.calculate_cost("tts", "unknown/model", character_count=100) is None


def test_dispatch_zero_usage() -> None:
    """Zero usage returns Decimal('0') for known models, not None."""
    assert catalog.calculate_cost(
        "llm", "openai/gpt-4o-mini", input_tokens=0, output_tokens=0
    ) == Decimal("0")
    assert catalog.calculate_cost("stt", "deepgram/nova-3", audio_seconds=0) == Decimal(
        "0"
    )
    assert catalog.calculate_cost(
        "tts", "cartesia/sonic-3", character_count=0
    ) == Decimal("0")


def test_dispatch_kwargs_for_other_modalities_ignored() -> None:
    """Only character_count drives a TTS price: 1000 * $0.000015 = $0.015."""
    cost = catalog.calculate_cost(
        "tts",
        "openai/tts-1",
        audio_seconds=99,
        input_tokens=99,
        output_tokens=99,
        character_count=1000,
    )
    assert cost == Decimal("0.015")


@pytest.mark.parametrize(
    "modality,expected",
    [
        ("llm", llm.PRICING_SOURCE),
        ("stt", stt.PRICING_SOURCE),
        ("tts", tts.PRICING_SOURCE),
        ("foo", "unknown"),
        ("", "unknown"),
    ],
)
def test_pricing_source(modality: str, expected: str) -> None:
    assert catalog.pricing_source(modality) == expected
    if expected != "unknown":
        assert expected.startswith("voice-prices@")
