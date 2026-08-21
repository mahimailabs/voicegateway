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

# A model can MATCH the catalogue and still carry no rate for the units
# recorded: ``deepgram/nova-general`` matches the entry ``nova``, whose
# ModelPrice has every field None. calc_price applies nothing, raises nothing,
# and returns a total of 0. Stamping that row ``voice-prices@<version>`` claims
# the catalogue priced it, which it did not, and the row then reads exactly
# like a model that genuinely costs nothing.
#
# This source says only what is true: the catalogue matched the model and put
# no rate to the units recorded. It deliberately does NOT claim to know why.
# Of the 139 rateless entries in voice-prices 0.6.0, 131 are ``:free`` models
# where zero is the right answer and 8 are not, and the catalogue records no
# field distinguishing the two. Resolving that belongs upstream; until it is
# resolved, a meter that cannot tell them apart must not pick one.
UNRATED_SOURCE = "voice-prices-unrated"


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
    return calculate_cost_detail(
        modality,
        model,
        audio_seconds=audio_seconds,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        character_count=character_count,
    )[0]


def calculate_cost_detail(
    modality: str,
    model: str,
    *,
    audio_seconds: float = 0.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_input_tokens: int = 0,
    character_count: int = 0,
) -> tuple[Decimal | None, tuple[str, ...]]:
    """As :func:`calculate_cost`, plus the units the catalogue put no rate to.

    A non-empty second element means the returned total is NOT fully
    rate-backed and must not be reported as a priced figure. See
    :data:`UNRATED_SOURCE`.
    """
    if model.startswith(_SELF_HOSTED_PREFIXES):
        return Decimal("0"), ()
    if modality == "llm":
        return llm.calculate_llm_cost_detail(
            model, input_tokens, output_tokens, cached_input_tokens
        )
    if modality == "stt":
        return stt.calculate_stt_cost_detail(model, audio_seconds)
    if modality == "tts":
        return tts.calculate_tts_cost_detail(model, character_count)
    return None, ()


def pricing_source(modality: str) -> str:
    """Return the pricing-source attribution string for a modality."""
    if modality == "llm":
        return llm.PRICING_SOURCE
    if modality == "stt":
        return stt.PRICING_SOURCE
    if modality == "tts":
        return tts.PRICING_SOURCE
    return "unknown"
