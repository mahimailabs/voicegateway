"""Unit tests for voicegateway.pricing.catalog.

Covers the unified facade: modality dispatch correctness for
calculate_cost(), pricing_source() per-modality attribution, and
unknown-modality fallback behavior. Also covers the legacy
PRICING / get_pricing API kept temporarily until Phase 2.5 cleanup.
"""

from __future__ import annotations

from decimal import Decimal

from voicegateway.pricing import catalog, llm, stt, tts

# ----- calculate_cost dispatch -----------------------------------------


def test_dispatch_llm_routes_to_llm_module() -> None:
    """LLM modality dispatch returns same value as direct llm.calculate_llm_cost."""
    direct = llm.calculate_llm_cost("openai/gpt-4o", 1000, 100)
    via_facade = catalog.calculate_cost(
        "llm", "openai/gpt-4o", input_tokens=1000, output_tokens=100
    )
    assert direct is not None
    assert via_facade == direct


def test_dispatch_stt_routes_to_stt_module() -> None:
    """STT dispatch returns same value as direct stt.calculate_stt_cost."""
    direct = stt.calculate_stt_cost("deepgram/nova-3", 60)
    via_facade = catalog.calculate_cost(
        "stt", "deepgram/nova-3", audio_seconds=60
    )
    assert via_facade == direct
    assert via_facade == Decimal("0.0043")


def test_dispatch_tts_routes_to_tts_module() -> None:
    """TTS dispatch returns same value as direct tts.calculate_tts_cost."""
    direct = tts.calculate_tts_cost("cartesia/sonic-3", 1000)
    via_facade = catalog.calculate_cost(
        "tts", "cartesia/sonic-3", character_count=1000
    )
    assert via_facade == direct
    assert via_facade == Decimal("0.065")


def test_dispatch_unknown_modality_returns_none() -> None:
    """Unknown modality returns None (catalog facade fallback)."""
    assert catalog.calculate_cost("foo", "bar/baz") is None
    assert catalog.calculate_cost("", "deepgram/nova-3") is None


def test_dispatch_unknown_model_propagates_none() -> None:
    """Known modality + unknown model -> None (no silent zero)."""
    assert (
        catalog.calculate_cost("llm", "foo/bar-baz", input_tokens=100) is None
    )
    assert (
        catalog.calculate_cost("stt", "unknown/model", audio_seconds=60) is None
    )
    assert (
        catalog.calculate_cost("tts", "unknown/model", character_count=100)
        is None
    )


def test_dispatch_zero_usage() -> None:
    """Zero usage returns Decimal('0') for known models, not None."""
    assert (
        catalog.calculate_cost(
            "llm", "openai/gpt-4o-mini", input_tokens=0, output_tokens=0
        )
        == Decimal("0")
    )
    assert (
        catalog.calculate_cost("stt", "deepgram/nova-3", audio_seconds=0)
        == Decimal("0")
    )
    assert (
        catalog.calculate_cost("tts", "cartesia/sonic-3", character_count=0)
        == Decimal("0")
    )


def test_dispatch_kwargs_for_other_modalities_ignored() -> None:
    """Calling with mismatched kwargs still works; the unused ones are ignored."""
    # Pass all four kwargs even though only character_count matters for tts.
    cost = catalog.calculate_cost(
        "tts",
        "openai/tts-1",
        audio_seconds=99,
        input_tokens=99,
        output_tokens=99,
        character_count=1000,
    )
    # Only character_count should drive the calc: 1000 * $0.000015 = $0.015
    assert cost == Decimal("0.015")


# ----- pricing_source --------------------------------------------------


def test_pricing_source_llm() -> None:
    """pricing_source('llm') == llm.PRICING_SOURCE."""
    assert catalog.pricing_source("llm") == llm.PRICING_SOURCE
    assert catalog.pricing_source("llm").startswith("genai-prices@")


def test_pricing_source_stt() -> None:
    """pricing_source('stt') == stt.PRICING_SOURCE."""
    assert catalog.pricing_source("stt") == stt.PRICING_SOURCE
    assert catalog.pricing_source("stt").startswith("voicegateway-catalog@")


def test_pricing_source_tts() -> None:
    """pricing_source('tts') == tts.PRICING_SOURCE."""
    assert catalog.pricing_source("tts") == tts.PRICING_SOURCE
    assert catalog.pricing_source("tts").startswith("voicegateway-catalog@")


def test_pricing_source_unknown_modality() -> None:
    """Unknown modality returns the literal 'unknown' string."""
    assert catalog.pricing_source("foo") == "unknown"
    assert catalog.pricing_source("") == "unknown"


# ----- legacy API (PRICING dict + get_pricing) -------------------------


def test_legacy_pricing_dict_present() -> None:
    """Legacy PRICING dict is kept until Phase 2.5 cleanup."""
    assert isinstance(catalog.PRICING, dict)
    assert {"stt", "llm", "tts"}.issubset(catalog.PRICING.keys())


def test_legacy_get_pricing_known_stt() -> None:
    """Legacy get_pricing returns a dict with per_minute for known STT models."""
    pricing = catalog.get_pricing("deepgram/nova-3", "stt")
    assert pricing == {"per_minute": 0.0043}


def test_legacy_get_pricing_known_llm() -> None:
    """Legacy get_pricing returns input_per_1k/output_per_1k for known LLM models."""
    pricing = catalog.get_pricing("openai/gpt-4.1-mini", "llm")
    assert pricing == {"input_per_1k": 0.0004, "output_per_1k": 0.0016}


def test_legacy_get_pricing_known_tts() -> None:
    """Legacy get_pricing returns per_character for known TTS models."""
    pricing = catalog.get_pricing("cartesia/sonic-3", "tts")
    assert pricing == {"per_character": 0.000065}


def test_legacy_get_pricing_unknown_returns_empty() -> None:
    """Legacy get_pricing returns {} for unknown model or modality."""
    assert catalog.get_pricing("unknown/model", "stt") == {}
    assert catalog.get_pricing("deepgram/nova-3", "unknown-modality") == {}
