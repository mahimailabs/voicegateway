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

The legacy `PRICING` dict and `get_pricing()` at the bottom of this
file are kept temporarily so the existing CostTracker (which still
imports `get_pricing`) keeps working until Phase 2.3 wires it through
`calculate_cost()` and removes them.
"""

from __future__ import annotations

from decimal import Decimal

from voicegateway.pricing import llm, stt, tts

# ---------------------------------------------------------------------
# v0.1.0 facade
# ---------------------------------------------------------------------


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


# ---------------------------------------------------------------------
# Legacy v0.0.x API. DEPRECATED. Phase 2.3 wires CostTracker through
# `calculate_cost` above and removes the rest of this file. Until
# then this dict and `get_pricing` keep the existing tests green.
# ---------------------------------------------------------------------

PRICING: dict[str, dict[str, dict[str, float]]] = {
    "stt": {
        "deepgram/nova-3": {"per_minute": 0.0043},
        "deepgram/nova-2": {"per_minute": 0.0043},
        "deepgram/flux-general": {"per_minute": 0.0043},
        "assemblyai/universal-2": {"per_minute": 0.005},
        "openai/whisper-1": {"per_minute": 0.006},
        "groq/whisper-large-v3": {"per_minute": 0.0},
        "local/whisper-large-v3": {"per_minute": 0.0},
        "local/whisper-turbo": {"per_minute": 0.0},
        "local/whisper-base": {"per_minute": 0.0},
    },
    "llm": {
        "openai/gpt-4.1-mini": {"input_per_1k": 0.0004, "output_per_1k": 0.0016},
        "openai/gpt-4o": {"input_per_1k": 0.005, "output_per_1k": 0.015},
        "openai/gpt-4o-mini": {"input_per_1k": 0.00015, "output_per_1k": 0.0006},
        "anthropic/claude-3.5-sonnet": {"input_per_1k": 0.003, "output_per_1k": 0.015},
        "groq/llama-3.1-70b": {"input_per_1k": 0.00059, "output_per_1k": 0.00079},
        "groq/llama-3.1-8b": {"input_per_1k": 0.0, "output_per_1k": 0.0},
        "ollama/qwen2.5:3b": {"input_per_1k": 0.0, "output_per_1k": 0.0},
        "ollama/qwen2.5:7b": {"input_per_1k": 0.0, "output_per_1k": 0.0},
        "ollama/llama3.2:3b": {"input_per_1k": 0.0, "output_per_1k": 0.0},
        "ollama/phi4-mini": {"input_per_1k": 0.0, "output_per_1k": 0.0},
    },
    "tts": {
        "cartesia/sonic-3": {"per_character": 0.000065},
        "elevenlabs/eleven_turbo_v2_5": {"per_character": 0.00018},
        "deepgram/aura-2": {"per_character": 0.000065},
        "openai/tts-1": {"per_character": 0.000015},
        "local/kokoro": {"per_character": 0.0},
        "local/piper": {"per_character": 0.0},
    },
}


def get_pricing(model_id: str, modality: str) -> dict[str, float]:
    """Get pricing for a model.

    DEPRECATED: Phase 2.3 wires CostTracker through `calculate_cost`
    above and removes this function.

    Returns:
        Pricing dict, or empty dict with zero values if model not in
        catalog.
    """
    modality_pricing = PRICING.get(modality, {})
    return modality_pricing.get(model_id, {})
