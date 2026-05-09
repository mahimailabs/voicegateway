"""STT pricing via a local source-date-tagged catalog.

genai-prices is LLM-focused and does not cover STT, so VG keeps STT
pricing in this small local catalog. Each entry carries:

- `per_minute`: USD per minute of audio (Decimal for exact arithmetic).
- `pricing_source_date`: the date the maintainer last verified the
  rate against the provider's pricing page. Per-entry so a refresh of
  one provider does not lie about the other entries' freshness.
- `pricing_source_url`: that pricing page.

`tests/pricing/test_staleness.py` enforces a 60-day staleness gate in
CI: any entry whose ``pricing_source_date`` is more than 60 days
older than ``date.today()`` fails the build, forcing per-entry
refreshes. See `docs/contributing/refreshing-pricing.md` for the
maintenance flow.
"""

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
        # Groq Whisper Large v3 is published as $0.111/hour, i.e.
        # $0.00185/minute. Replaces the v0.0.x catalog's $0.0
        # placeholder which silently under-counted Groq STT cost.
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
    """Return the oldest `pricing_source_date` in the catalog.

    Surfaced via PRICING_SOURCE so the per-request attribution string
    is honest about worst-case freshness.
    """
    return min(entry.pricing_source_date for entry in CATALOG.values())


PRICING_SOURCE = f"voicegateway-catalog@{_oldest_pricing_date().isoformat()}"


def calculate_stt_cost(model: str, audio_seconds: float) -> Decimal | None:
    """Return total STT cost in USD, or None if the model is unknown.

    Args:
        model: VoiceGateway STT model ID, e.g. "deepgram/nova-3" or
            "local/whisper-large-v3".
        audio_seconds: Audio duration in seconds.

    Returns:
        Decimal total price in USD when the model is in the catalog,
        otherwise None. Never returns Decimal("0") for an unknown
        model; matches the LLM module's no-silent-zero contract so
        callers can distinguish "free" from "unknown".
    """
    if audio_seconds < 0:
        raise ValueError(
            f"audio_seconds must be non-negative, got {audio_seconds}"
        )
    entry = CATALOG.get(model)
    if entry is None:
        return None
    minutes = Decimal(str(audio_seconds)) / Decimal(60)
    return minutes * entry.per_minute
