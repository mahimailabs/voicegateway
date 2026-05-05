"""Unit tests for voicegateway.pricing.stt.

Covers the local STT catalog: known/unknown models, source-date and
source-URL metadata, PRICING_SOURCE format, Decimal precision.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from voicegateway.pricing import stt


def test_pricing_source_format() -> None:
    """PRICING_SOURCE follows the documented `voicegateway-catalog@<date>` shape."""
    assert stt.PRICING_SOURCE.startswith("voicegateway-catalog@")
    date_str = stt.PRICING_SOURCE.split("@", 1)[1]
    parsed = date.fromisoformat(date_str)
    assert isinstance(parsed, date)


def test_pricing_source_uses_oldest_entry_date() -> None:
    """Worst-case freshness: PRICING_SOURCE reflects the oldest entry."""
    oldest = min(entry.pricing_source_date for entry in stt.CATALOG.values())
    assert stt.PRICING_SOURCE == f"voicegateway-catalog@{oldest.isoformat()}"


def test_catalog_not_empty() -> None:
    assert stt.CATALOG, "STT catalog should have at least one entry"


def test_every_entry_has_required_metadata() -> None:
    """Each entry carries per_minute Decimal, source date, source URL."""
    for model_id, entry in stt.CATALOG.items():
        assert isinstance(entry.per_minute, Decimal), (
            f"{model_id}: per_minute should be Decimal"
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


def test_deepgram_nova_3_priced_correctly() -> None:
    """60 audio-seconds = 1 minute × $0.0043 = $0.0043."""
    assert stt.calculate_stt_cost("deepgram/nova-3", 60) == Decimal("0.0043")


def test_groq_whisper_no_longer_silent_zero() -> None:
    """Phase 2.5 regression guard: groq/whisper-large-v3 has paid-tier pricing."""
    cost = stt.calculate_stt_cost("groq/whisper-large-v3", 3600)
    # 60 min × $0.00185/min = $0.111 (matches Groq's published $0.111/hour)
    assert cost == Decimal("0.111")


def test_local_models_priced_zero() -> None:
    """local/* models are structurally free."""
    assert stt.calculate_stt_cost("local/whisper-large-v3", 60) == Decimal("0")
    assert stt.calculate_stt_cost("local/whisper-base", 3600) == Decimal("0")
    assert stt.calculate_stt_cost("local/whisper-turbo", 1) == Decimal("0")


def test_unknown_model_returns_none() -> None:
    """Unknown model returns None (no silent zero contract)."""
    assert stt.calculate_stt_cost("foo/bar-baz", 60) is None


def test_zero_seconds_returns_zero_decimal() -> None:
    """Zero usage returns Decimal('0'), not None."""
    cost = stt.calculate_stt_cost("deepgram/nova-3", 0)
    assert cost is not None
    assert cost == Decimal("0")


def test_fractional_minute() -> None:
    """30 seconds resolves to half a minute via exact Decimal."""
    # 0.5 min × $0.0043 = $0.00215
    assert stt.calculate_stt_cost("deepgram/nova-3", 30) == Decimal("0.00215")


def test_one_hour() -> None:
    """3600 seconds = 60 min × $0.0043 = $0.258."""
    assert stt.calculate_stt_cost("deepgram/nova-3", 3600) == Decimal("0.258")


def test_return_type_is_decimal() -> None:
    cost = stt.calculate_stt_cost("deepgram/nova-3", 60)
    assert isinstance(cost, Decimal)


def test_expected_models_present() -> None:
    """Standard providers all in catalog (regression guard against accidental drops)."""
    expected = {
        "deepgram/nova-3",
        "deepgram/nova-2",
        "openai/whisper-1",
        "assemblyai/universal-2",
        "groq/whisper-large-v3",
        "local/whisper-large-v3",
        "local/whisper-base",
        "local/whisper-turbo",
    }
    assert expected.issubset(set(stt.CATALOG.keys()))
