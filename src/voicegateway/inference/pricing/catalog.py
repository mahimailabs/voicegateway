"""Pricing facade. Dispatches by modality."""

from __future__ import annotations

from decimal import Decimal

from voicegateway.inference.pricing import llm, stt, tts


def calculate_cost(
    modality: str,
    model: str,
    *,
    audio_seconds: float = 0.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    character_count: int = 0,
) -> Decimal | None:
    """Calculate the cost of a single request."""
    if modality == "llm":
        return llm.calculate_llm_cost(model, input_tokens, output_tokens)
    if modality == "stt":
        return stt.calculate_stt_cost(model, audio_seconds)
    if modality == "tts":
        return tts.calculate_tts_cost(model, character_count)
    return None


def pricing_source(modality: str) -> str:
    """Return the pricing-source attribution string for a modality."""
    if modality == "llm":
        return llm.PRICING_SOURCE
    if modality == "stt":
        return stt.PRICING_SOURCE
    if modality == "tts":
        return tts.PRICING_SOURCE
    return "unknown"
