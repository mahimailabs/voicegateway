"""Staleness gate for STT and TTS catalogs (Phase 2.6).

Fails CI when any entry's `pricing_source_date` is more than 60 days
old. Forces a manual refresh of the catalog with each release:
maintainers re-verify the rate against the linked
`pricing_source_url` and bump the date. genai-prices handles LLM
pricing freshness upstream, so the gate only covers the local
catalogs (`voicegateway/pricing/stt.py` and `voicegateway/pricing/tts.py`).
"""

from __future__ import annotations

from datetime import date, timedelta

from voicegateway.pricing.stt import CATALOG as STT_CATALOG
from voicegateway.pricing.tts import CATALOG as TTS_CATALOG

_MAX_AGE_DAYS = 60


def _stale_entries(catalog: dict, today: date) -> list[tuple[str, int]]:
    """Return [(model_id, days_old)] for entries older than _MAX_AGE_DAYS."""
    cutoff = today - timedelta(days=_MAX_AGE_DAYS)
    stale = []
    for model_id, entry in catalog.items():
        if entry.pricing_source_date < cutoff:
            stale.append((model_id, (today - entry.pricing_source_date).days))
    return stale


def _format_failure(modality: str, stale: list[tuple[str, int]]) -> str:
    rows = ", ".join(f"{m} ({d}d)" for m, d in stale)
    return (
        f"{modality} catalog has {len(stale)} entries older than "
        f"{_MAX_AGE_DAYS} days: {rows}. Re-verify each rate against "
        "the linked pricing_source_url and bump pricing_source_date."
    )


def test_stt_catalog_not_stale() -> None:
    stale = _stale_entries(STT_CATALOG, date.today())
    assert not stale, _format_failure("STT", stale)


def test_tts_catalog_not_stale() -> None:
    stale = _stale_entries(TTS_CATALOG, date.today())
    assert not stale, _format_failure("TTS", stale)
