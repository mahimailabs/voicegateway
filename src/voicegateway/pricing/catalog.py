"""Pricing facade. Dispatches by modality.

The v0.1.0 design splits pricing into modality-specific modules:

- `voicegateway/pricing/llm.py` wraps `pydantic/genai-prices` for LLM
  cost calculation.
- `voicegateway/pricing/stt.py` is a local source-date-tagged catalog
  for STT.
- `voicegateway/pricing/tts.py` is a local source-date-tagged catalog
  for TTS.

This module is the unified entry point. `calculate_cost()` routes to
the right module by modality, and `pricing_source()` returns the
per-modality attribution string for per-request logging.
"""

from __future__ import annotations

from decimal import Decimal

from voicegateway.pricing import llm, stt, tts


def calculate_cost(
    modality: str,
    model: str,
    *,
    audio_seconds: float = 0.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    character_count: int = 0,
) -> Decimal | None:
    """Calculate the cost of a single request.

    Dispatches by modality:

    - `"llm"` uses `input_tokens` and `output_tokens`.
    - `"stt"` uses `audio_seconds`.
    - `"tts"` uses `character_count`.

    Returns:
        Decimal total price in USD when the model is in the relevant
        catalog, otherwise None. Never returns Decimal("0") for an
        unknown model so callers can distinguish "free" from
        "unknown".
    """
    if modality == "llm":
        return llm.calculate_llm_cost(model, input_tokens, output_tokens)
    if modality == "stt":
        return stt.calculate_stt_cost(model, audio_seconds)
    if modality == "tts":
        return tts.calculate_tts_cost(model, character_count)
    return None


def pricing_source(modality: str) -> str:
    """Return the pricing-source attribution string for a modality.

    For per-request logging: tag every request with the source so
    reconciliation can verify which catalog produced the number.
    """
    if modality == "llm":
        return llm.PRICING_SOURCE
    if modality == "stt":
        return stt.PRICING_SOURCE
    if modality == "tts":
        return tts.PRICING_SOURCE
    return "unknown"
