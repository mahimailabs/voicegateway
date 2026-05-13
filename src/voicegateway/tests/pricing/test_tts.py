"""Unit tests for voicegateway.inference.pricing.tts."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from voicegateway.inference.pricing import tts


def test_pricing_source_format() -> None:
    """PRICING_SOURCE follows the documented `voicegateway-catalog@<date>` shape."""
    assert tts.PRICING_SOURCE.startswith("voicegateway-catalog@")
    date_str = tts.PRICING_SOURCE.split("@", 1)[1]
    parsed = date.fromisoformat(date_str)
    assert isinstance(parsed, date)


def test_pricing_source_uses_oldest_entry_date() -> None:
    """Worst-case freshness: PRICING_SOURCE reflects the oldest entry."""
    oldest = min(entry.pricing_source_date for entry in tts.CATALOG.values())
    assert tts.PRICING_SOURCE == f"voicegateway-catalog@{oldest.isoformat()}"


def test_catalog_not_empty() -> None:
    assert tts.CATALOG, "TTS catalog should have at least one entry"


def test_every_entry_has_required_metadata() -> None:
    """Each entry carries per_character Decimal, source date, source URL."""
    for model_id, entry in tts.CATALOG.items():
        assert isinstance(entry.per_character, Decimal), (
            f"{model_id}: per_character should be Decimal"
        )
        assert isinstance(entry.pricing_source_date, date), (
            f"{model_id}: pricing_source_date should be a date"
        )
        assert entry.pricing_source_url, (
            f"{model_id}: pricing_source_url should be non-empty"
        )
        assert entry.pricing_source_url.startswith(("http://", "https://")), (
            f"{model_id}: pricing_source_url should be an http(s) URL, "
            f"got {entry.pricing_source_url!r}"
        )


def test_cartesia_priced_correctly() -> None:
    """1000 chars × $0.000065 = $0.065 (estimate; reconcile against invoice)."""
    assert tts.calculate_tts_cost("cartesia/sonic-3", 1000) == Decimal("0.065")


def test_elevenlabs_priced_correctly() -> None:
    """1000 chars × $0.00018 = $0.18."""
    assert tts.calculate_tts_cost("elevenlabs/eleven_turbo_v2_5", 1000) == Decimal(
        "0.18"
    )


def test_openai_tts1_priced_correctly() -> None:
    """1000 chars × $0.000015 = $0.015."""
    assert tts.calculate_tts_cost("openai/tts-1", 1000) == Decimal("0.015")


def test_local_models_priced_zero() -> None:
    """local/* TTS models are structurally free."""
    assert tts.calculate_tts_cost("local/kokoro", 1000) == Decimal("0")
    assert tts.calculate_tts_cost("local/piper", 5000) == Decimal("0")


def test_unknown_model_returns_none() -> None:
    """Unknown model returns None (no silent zero contract)."""
    assert tts.calculate_tts_cost("foo/bar-baz", 1000) is None


def test_zero_chars_returns_zero_decimal() -> None:
    """Zero usage returns Decimal('0'), not None."""
    cost = tts.calculate_tts_cost("cartesia/sonic-3", 0)
    assert cost is not None
    assert cost == Decimal("0")


def test_large_character_count() -> None:
    """1M chars × $0.000065 = $65 via exact Decimal."""
    assert tts.calculate_tts_cost("cartesia/sonic-3", 1_000_000) == Decimal("65")


def test_return_type_is_decimal() -> None:
    cost = tts.calculate_tts_cost("cartesia/sonic-3", 100)
    assert isinstance(cost, Decimal)


def test_expected_models_present() -> None:
    """Standard providers all in catalog (regression guard)."""
    expected = {
        "cartesia/sonic-3",
        "elevenlabs/eleven_turbo_v2_5",
        "deepgram/aura-2",
        "openai/tts-1",
        "local/kokoro",
        "local/piper",
    }
    assert expected.issubset(set(tts.CATALOG.keys()))
