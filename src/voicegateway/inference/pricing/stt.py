"""STT pricing via a local source-date-tagged catalog."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class STTEntry:
    per_minute: Decimal
    pricing_source_date: date
    pricing_source_url: str


CATALOG: dict[str, STTEntry] = {
    "deepgram/nova-3": STTEntry(
        per_minute=Decimal("0.0043"),
        pricing_source_date=date(2026, 5, 4),
        pricing_source_url="https://deepgram.com/pricing",
    ),
    "deepgram/nova-2": STTEntry(
        per_minute=Decimal("0.0043"),
        pricing_source_date=date(2026, 5, 4),
        pricing_source_url="https://deepgram.com/pricing",
    ),
    "deepgram/flux-general": STTEntry(
        per_minute=Decimal("0.0043"),
        pricing_source_date=date(2026, 5, 4),
        pricing_source_url="https://deepgram.com/pricing",
    ),
    "assemblyai/universal-2": STTEntry(
        per_minute=Decimal("0.005"),
        pricing_source_date=date(2026, 5, 4),
        pricing_source_url="https://www.assemblyai.com/pricing",
    ),
    "openai/whisper-1": STTEntry(
        per_minute=Decimal("0.006"),
        pricing_source_date=date(2026, 5, 4),
        pricing_source_url="https://openai.com/api/pricing/",
    ),
    "groq/whisper-large-v3": STTEntry(
        per_minute=Decimal("0.00185"),
        pricing_source_date=date(2026, 5, 4),
        pricing_source_url="https://groq.com/pricing",
    ),
    "local/whisper-large-v3": STTEntry(
        per_minute=Decimal("0"),
        pricing_source_date=date(2026, 5, 4),
        pricing_source_url="https://github.com/openai/whisper",
    ),
    "local/whisper-turbo": STTEntry(
        per_minute=Decimal("0"),
        pricing_source_date=date(2026, 5, 4),
        pricing_source_url="https://github.com/openai/whisper",
    ),
    "local/whisper-base": STTEntry(
        per_minute=Decimal("0"),
        pricing_source_date=date(2026, 5, 4),
        pricing_source_url="https://github.com/openai/whisper",
    ),
}


def _oldest_pricing_date() -> date:
    """Return the oldest `pricing_source_date` in the catalog."""
    return min(entry.pricing_source_date for entry in CATALOG.values())


PRICING_SOURCE = f"voicegateway-catalog@{_oldest_pricing_date().isoformat()}"


def calculate_stt_cost(model: str, audio_seconds: float) -> Decimal | None:
    """Return total STT cost in USD, or None if the model is unknown."""
    if audio_seconds < 0:
        raise ValueError(f"audio_seconds must be non-negative, got {audio_seconds}")
    entry = CATALOG.get(model)
    if entry is None:
        return None
    minutes = Decimal(str(audio_seconds)) / Decimal(60)
    return minutes * entry.per_minute
