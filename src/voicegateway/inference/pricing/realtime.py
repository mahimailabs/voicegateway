"""Price distinct realtime measurements, never audio tokens as text tokens."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from voice_prices import Usage

from voicegateway.inference.pricing._calc import price_usage
from voicegateway.inference.pricing.llm import PRICING_SOURCE

# Published list-price fallback until voice-prices carries duration-based GPT-Live.
# Exact slug only: future models and snapshots must not inherit an assumed price.
# https://developers.openai.com/api/docs/models/gpt-live-1 (verified 2026-10-05)
_LIVE_MINUTE_USD = {"openai/gpt-live-1": Decimal("0.05")}
LIVE_PRICE_SOURCE = "openai-published:gpt-live-1:2026-10-05"


def price_realtime(
    model: str, quantities: dict[str, float | int], missing: list[str]
) -> tuple[Decimal | None, str]:
    """Return a complete estimate and provenance; None means incomplete pricing."""
    if missing:
        return None, ""
    try:
        values = {k: Decimal(str(v)) for k, v in quantities.items()}
    except (InvalidOperation, ValueError, TypeError):
        return None, ""
    if any(not value.is_finite() or value < 0 for value in values.values()):
        return None, ""
    if "audio_seconds" in values:
        if set(values) != {"audio_seconds"}:
            return None, ""
        rate = _LIVE_MINUTE_USD.get(model)
        if rate is None:
            return None, ""
        return values["audio_seconds"] * rate / Decimal(60), LIVE_PRICE_SOURCE
    text_dimensions = {"text_input", "text_output", "cache_read", "cache_write"}
    if set(values) == text_dimensions:
        if any(v != v.to_integral_value() for v in values.values()):
            return None, ""
        uncached = values["text_input"] - values["cache_read"] - values["cache_write"]
        if uncached < 0:
            return None, ""
        if model == "openai/gpt-5.6-luna":
            # Same dated official fallback as GPT-Live. Cache writes have a
            # separate premium; reasoning tokens are already in output tokens.
            # https://developers.openai.com/api/docs/models/gpt-5.6-luna
            input_multiplier = 2 if values["text_input"] > 272000 else 1
            output_multiplier = Decimal("1.5") if input_multiplier == 2 else Decimal(1)
            published_total = (
                (
                    uncached * Decimal("0.20")
                    + values["cache_read"] * Decimal("0.02")
                    + values["cache_write"] * Decimal("0.25")
                )
                * input_multiplier
                + values["text_output"] * Decimal("1.20") * output_multiplier
            ) / Decimal(1_000_000)
            return published_total, "openai-published:gpt-5.6-luna:2026-10-05"
        total, unrated = price_usage(
            Usage(
                input_tokens=int(values["text_input"]),
                output_tokens=int(values["text_output"]),
                cache_read_tokens=int(values["cache_read"]),
                cache_write_tokens=int(values["cache_write"]),
            ),
            model,
        )
        return (None, "") if total is None or unrated else (total, PRICING_SOURCE)
    required = {
        "text_input",
        "text_output",
        "cache_read",
        "realtime_audio_input",
        "realtime_audio_output",
        "realtime_audio_cache",
    }
    if set(values) != required or any(
        v != v.to_integral_value() for v in values.values()
    ):
        return None, ""
    if (
        values["cache_read"] > values["text_input"]
        or values["realtime_audio_cache"] > values["realtime_audio_input"]
    ):
        return None, ""
    # voice-prices expects inclusive parent totals; audio and cache fields
    # are children, not additional usage on top of input/output tokens.
    usage = Usage(
        input_tokens=int(values["text_input"] + values["realtime_audio_input"]),
        output_tokens=int(values["text_output"] + values["realtime_audio_output"]),
        cache_read_tokens=int(values["cache_read"] + values["realtime_audio_cache"]),
        input_audio_tokens=int(values["realtime_audio_input"]),
        output_audio_tokens=int(values["realtime_audio_output"]),
        cache_audio_read_tokens=int(values["realtime_audio_cache"]),
    )
    total, unrated = price_usage(usage, model)
    return (None, "") if total is None or unrated else (total, PRICING_SOURCE)
