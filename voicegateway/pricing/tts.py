"""TTS pricing via a local source-date-tagged catalog.

genai-prices is LLM-focused and does not cover TTS, so VG keeps TTS
pricing in this small local catalog. Each entry carries:

- `per_character`: USD per character (Decimal for exact arithmetic).
- `pricing_source_date`: the date the rate was last verified against
  the provider's pricing page.
- `pricing_source_url`: that pricing page.

Phase 2.6 enforces a 60-day staleness gate in CI: any entry whose
`pricing_source_date` is more than 60 days old fails the build,
forcing a manual refresh per release. This trades ongoing
maintenance for an honest, verifiable catalog.

A note on credit-based providers (Cartesia, in particular):
Cartesia bills by audio-seconds via a credit system rather than by
input characters. The per-character rate stored here is an estimate
that depends on the user's plan tier and the audio characteristics
of their workload (characters-per-second varies). Estimates can
drift by tens of percent. Reconcile against your provider invoice
via `voicegw reconcile` (Phase 4) to verify; the per-request
attribution string makes this auditable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class TTSEntry:
    per_character: Decimal
    pricing_source_date: date
    pricing_source_url: str


# Refresh date for the v0.1.0 baseline.
_REFRESHED = date(2026, 5, 4)

CATALOG: dict[str, TTSEntry] = {
    "cartesia/sonic-3": TTSEntry(
        # ESTIMATE. Cartesia bills by audio-seconds via credits,
        # not per-character. This per-character rate is the v0.0.x
        # catalog estimate held over for v0.1.0; expect drift of
        # tens of percent depending on plan tier and audio cps.
        # Reconcile against your invoice for accurate FinOps.
        per_character=Decimal("0.000065"),
        pricing_source_date=_REFRESHED,
        pricing_source_url="https://cartesia.ai/pricing",
    ),
    "elevenlabs/eleven_turbo_v2_5": TTSEntry(
        per_character=Decimal("0.00018"),
        pricing_source_date=_REFRESHED,
        pricing_source_url="https://elevenlabs.io/pricing",
    ),
    "deepgram/aura-2": TTSEntry(
        per_character=Decimal("0.000065"),
        pricing_source_date=_REFRESHED,
        pricing_source_url="https://deepgram.com/pricing",
    ),
    "openai/tts-1": TTSEntry(
        per_character=Decimal("0.000015"),
        pricing_source_date=_REFRESHED,
        pricing_source_url="https://openai.com/api/pricing/",
    ),
    "local/kokoro": TTSEntry(
        per_character=Decimal("0"),
        pricing_source_date=_REFRESHED,
        pricing_source_url="https://github.com/hexgrad/kokoro",
    ),
    "local/piper": TTSEntry(
        per_character=Decimal("0"),
        pricing_source_date=_REFRESHED,
        pricing_source_url="https://github.com/rhasspy/piper",
    ),
}


def _oldest_pricing_date() -> date:
    """Return the oldest `pricing_source_date` in the catalog.

    Surfaced via PRICING_SOURCE so the per-request attribution string
    is honest about worst-case freshness.
    """
    return min(entry.pricing_source_date for entry in CATALOG.values())


PRICING_SOURCE = f"voicegateway-catalog@{_oldest_pricing_date().isoformat()}"


def calculate_tts_cost(model: str, character_count: int) -> Decimal | None:
    """Return total TTS cost in USD, or None if the model is unknown.

    Args:
        model: VoiceGateway TTS model ID, e.g. "cartesia/sonic-3" or
            "local/kokoro".
        character_count: Number of characters synthesized.

    Returns:
        Decimal total price in USD when the model is in the catalog,
        otherwise None. Never returns Decimal("0") for an unknown
        model; matches the LLM and STT modules' no-silent-zero
        contract so callers can distinguish "free" from "unknown".

    Note: estimates for credit-based providers (e.g. Cartesia) may
    drift by tens of percent. Reconcile via `voicegw reconcile`
    (Phase 4) for FinOps-grade accuracy.
    """
    entry = CATALOG.get(model)
    if entry is None:
        return None
    return Decimal(character_count) * entry.per_character
