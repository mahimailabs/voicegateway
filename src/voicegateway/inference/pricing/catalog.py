"""Pricing facade. Dispatches by modality."""

from __future__ import annotations

from decimal import Decimal

from voicegateway.inference.pricing import llm, stt, tts

# Self-hosted models (``local/*`` and ``ollama/*``) run on the operator's own
# hardware, so there is no per-unit cloud rate: they always price at $0. This
# source tag distinguishes "free because self-hosted" from "unpriced because
# unknown" (empty string) in recorded request rows.
SELF_HOSTED_SOURCE = "voicegateway-local"
_SELF_HOSTED_PREFIXES = ("local/", "ollama/")


def calculate_cost(
    modality: str,
    model: str,
    *,
    audio_seconds: float = 0.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_input_tokens: int = 0,
    character_count: int = 0,
) -> Decimal | None:
    """Calculate the cost of a single request.

    Self-hosted models (``local/*``, ``ollama/*``) always return
    ``Decimal('0')``. Cloud models are priced by voice-prices; an unknown model
    returns ``None``.
    """
    if model.startswith(_SELF_HOSTED_PREFIXES):
        return Decimal("0")
    if modality == "llm":
        return llm.calculate_llm_cost(
            model, input_tokens, output_tokens, cached_input_tokens
        )
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
