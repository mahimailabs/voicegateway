"""End-to-end test that prompt_cached_tokens flows through to genai-prices.

LK reports cached prompt tokens in LLMMetrics.prompt_cached_tokens.
genai-prices applies a discount (OpenAI 50%, Anthropic ~10%) when those
tokens are passed via Usage.cache_read_tokens. This file asserts the full
chain:

    LK metric.prompt_cached_tokens
        -> _extract_units (3-tuple)
        -> _log_request(cached_input_units=...)
        -> cost_tracker.create_record(cached_input_units=...)
        -> cost_tracker.calculate_cost(cached_input_units=...)
        -> catalog.calculate_cost(cached_input_tokens=...)
        -> llm.calculate_llm_cost(cached_input_tokens=...)
        -> Usage(input_tokens=fresh, cache_read_tokens=cached)
        -> genai-prices applies the per-provider discount.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from voicegateway.inference.pricing import llm as llm_pricing
from voicegateway.middleware.cost_tracker_middleware import CostTracker
from voicegateway.middleware.instrumented_provider_middleware import (
    _InstrumentedBase,
    wrap_provider,
)
from voicegateway.models.request_model import RequestRecord


def _make_llm_wrapper(cost_tracker: CostTracker) -> _InstrumentedBase:
    """Build an instrumented LLM wrapper using the real CostTracker."""
    storage = MagicMock()
    storage.log_request = AsyncMock()

    wrapper = wrap_provider(
        instance=MagicMock(),
        modality="llm",
        model_id="openai/gpt-4o-mini",
        provider="openai",
        project="smoke",
        cost_tracker=cost_tracker,
        storage=storage,
    )
    assert isinstance(wrapper, _InstrumentedBase)
    return wrapper


async def _drain(wrapper) -> None:
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    pending = list(wrapper._pending_log_tasks)
    if pending:
        await asyncio.gather(*pending)


async def test_cached_tokens_propagate_to_record_and_storage() -> None:
    """metric.prompt_cached_tokens flows into the stored RequestRecord."""
    cost_tracker = CostTracker(storage=None)
    wrapper = _make_llm_wrapper(cost_tracker)

    metric = SimpleNamespace(
        prompt_tokens=1000,
        completion_tokens=200,
        prompt_cached_tokens=800,
        cancelled=False,
    )
    wrapper._on_metrics_log(metric)
    await _drain(wrapper)

    storage = object.__getattribute__(wrapper, "_storage")
    assert storage.log_request.call_count == 1
    record = storage.log_request.call_args.args[0]
    assert isinstance(record, RequestRecord)
    assert record.input_units == pytest.approx(1000.0)
    assert record.output_units == pytest.approx(200.0)
    assert record.cached_input_units == pytest.approx(800.0)


def test_cache_discount_reduces_cost_vs_uncached() -> None:
    """A cache-heavy LLM call costs strictly less than the same call uncached.

    Uses the real genai-prices catalog. The exact discount differs per
    provider (OpenAI is 50%); asserting cached < uncached is robust to
    rate changes upstream.
    """
    uncached = llm_pricing.calculate_llm_cost(
        "openai/gpt-4o-mini",
        input_tokens=1000,
        output_tokens=200,
        cached_input_tokens=0,
    )
    cached = llm_pricing.calculate_llm_cost(
        "openai/gpt-4o-mini",
        input_tokens=1000,
        output_tokens=200,
        cached_input_tokens=800,
    )
    assert uncached is not None, (
        "test depends on openai/gpt-4o-mini pricing being known"
    )
    assert cached is not None
    assert cached < uncached, (
        "cache_read_tokens path did not apply the provider discount; "
        f"uncached={uncached!r}, cached={cached!r}"
    )


def test_zero_cached_tokens_matches_no_cached_arg() -> None:
    """Passing cached_input_tokens=0 must equal omitting it entirely."""
    explicit_zero = llm_pricing.calculate_llm_cost(
        "openai/gpt-4o-mini",
        input_tokens=500,
        output_tokens=100,
        cached_input_tokens=0,
    )
    omitted = llm_pricing.calculate_llm_cost(
        "openai/gpt-4o-mini",
        input_tokens=500,
        output_tokens=100,
    )
    assert explicit_zero == omitted, (
        "the cached path must be a no-op when cached_input_tokens=0; "
        "any drift here means we accidentally pass cache_read_tokens=0 "
        "and genai-prices treats that as a billable signal"
    )


def test_cached_greater_than_prompt_is_clamped() -> None:
    """If the metric reports more cached than prompt (sanity bug), clamp.

    Guards against a malformed LK metric crashing the price call with a
    negative ``input_tokens`` after subtraction.
    """
    cost = llm_pricing.calculate_llm_cost(
        "openai/gpt-4o-mini",
        input_tokens=100,
        output_tokens=50,
        cached_input_tokens=999,  # absurd: cached > prompt
    )
    # No exception raised; a price is returned (or None if model unknown).
    assert cost is None or cost >= 0
