"""TTS pricing via a local source-date-tagged catalog."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class TTSEntry:
    per_character: Decimal
    pricing_source_date: date
    pricing_source_url: str


CATALOG: dict[str, TTSEntry] = {
    "cartesia/sonic-3": TTSEntry(
        per_character=Decimal("0.000065"),
        pricing_source_date=date(2026, 5, 4),
        pricing_source_url="https://cartesia.ai/pricing",
    ),
    "elevenlabs/eleven_turbo_v2_5": TTSEntry(
        per_character=Decimal("0.00018"),
        pricing_source_date=date(2026, 5, 4),
        pricing_source_url="https://elevenlabs.io/pricing",
    ),
    "deepgram/aura-2": TTSEntry(
        per_character=Decimal("0.000065"),
        pricing_source_date=date(2026, 5, 4),
        pricing_source_url="https://deepgram.com/pricing",
    ),
    "openai/tts-1": TTSEntry(
        per_character=Decimal("0.000015"),
        pricing_source_date=date(2026, 5, 4),
        pricing_source_url="https://openai.com/api/pricing/",
    ),
    "local/kokoro": TTSEntry(
        per_character=Decimal("0"),
        pricing_source_date=date(2026, 5, 4),
        pricing_source_url="https://github.com/hexgrad/kokoro",
    ),
    "local/piper": TTSEntry(
        per_character=Decimal("0"),
        pricing_source_date=date(2026, 5, 4),
        pricing_source_url="https://github.com/rhasspy/piper",
    ),
}


def _oldest_pricing_date() -> date:
    """Return the oldest `pricing_source_date` in the catalog."""
    return min(entry.pricing_source_date for entry in CATALOG.values())


PRICING_SOURCE = f"voicegateway-catalog@{_oldest_pricing_date().isoformat()}"


def calculate_tts_cost(model: str, character_count: int) -> Decimal | None:
    """Return total TTS cost in USD, or None if the model is unknown."""
    if character_count < 0:
        raise ValueError(f"character_count must be non-negative, got {character_count}")
    entry = CATALOG.get(model)
    if entry is None:
        return None
    return Decimal(character_count) * entry.per_character
