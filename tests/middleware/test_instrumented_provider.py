"""Tests for the _InstrumentedBase TTFB hook contract.

The audit found that `_mark_first_byte` is a manual hook the streaming
code paths in each modality wrapper must call; if a future refactor
forgets to call it, TTFB silently becomes total latency. These tests
exercise the hook itself (not the per-provider code paths that should
call it) so a refactor that breaks the mechanism gets caught:

- TTFB reflects start -> first_byte when the hook is called.
- TTFB falls back to total latency when the hook is not called.
- The hook is idempotent: subsequent calls do not overwrite the
  first-byte timestamp.

Layer-B coverage (the per-provider streaming code paths actually
calling the hook at the right moment) lands when streaming fixtures
arrive and the wrapper-replay tests are wired up; see
`tests/test_streaming_cost_accounting.py`.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

from voicegateway.middleware.instrumented_provider import InstrumentedSTT


def _make_wrapper() -> InstrumentedSTT:
    """Build an instrumented STT wrapper with mocked cost tracker + wrapped instance."""
    cost_tracker = MagicMock()
    cost_tracker.create_record = MagicMock(return_value=MagicMock())
    cost_tracker.notify_spend = AsyncMock()
    return InstrumentedSTT(
        wrapped=MagicMock(),
        model_id="test/model",
        provider="test",
        project="default",
        cost_tracker=cost_tracker,
        storage=None,
    )


def test_first_byte_starts_unset() -> None:
    """`_first_byte_time` is None at construction; only `_mark_first_byte` sets it."""
    wrapper = _make_wrapper()
    assert object.__getattribute__(wrapper, "_first_byte_time") is None


def test_mark_first_byte_records_a_timestamp() -> None:
    """Calling `_mark_first_byte` writes a perf_counter timestamp."""
    wrapper = _make_wrapper()
    wrapper._mark_first_byte()
    first_byte = object.__getattribute__(wrapper, "_first_byte_time")
    assert first_byte is not None
    assert first_byte > 0


def test_mark_first_byte_is_idempotent() -> None:
    """Subsequent calls do NOT overwrite the first-byte timestamp."""
    wrapper = _make_wrapper()
    wrapper._mark_first_byte()
    first = object.__getattribute__(wrapper, "_first_byte_time")
    time.sleep(0.005)
    wrapper._mark_first_byte()
    second = object.__getattribute__(wrapper, "_first_byte_time")
    assert first == second, (
        "Idempotency contract: only the first call records. If a future refactor "
        "drops the `is None` guard, this test catches it before the change ships."
    )


async def test_log_request_records_ttfb_when_first_byte_marked() -> None:
    """`_log_request` records ttfb_ms < total_latency_ms when the hook fired partway."""
    wrapper = _make_wrapper()
    cost_tracker = object.__getattribute__(wrapper, "_cost_tracker")

    # Wait briefly, mark first byte, then wait longer before logging so ttfb is
    # measurably less than total_latency.
    await asyncio.sleep(0.005)
    wrapper._mark_first_byte()
    await asyncio.sleep(0.020)

    await wrapper._log_request(input_units=1.0)

    create_record_kwargs = cost_tracker.create_record.call_args.kwargs
    ttfb_ms = create_record_kwargs["ttfb_ms"]
    total_ms = create_record_kwargs["total_latency_ms"]

    assert ttfb_ms > 0
    assert ttfb_ms < total_ms, (
        f"ttfb_ms ({ttfb_ms:.2f}) should be less than total_latency_ms ({total_ms:.2f}) "
        "when _mark_first_byte was called partway through. If they are equal, the "
        "hook recorded the end time instead of the first-byte time, OR the wrapper "
        "is not consulting `_first_byte_time` in `_log_request`."
    )


async def test_log_request_falls_back_to_total_when_hook_not_called() -> None:
    """When the hook never fires, `ttfb_ms == total_latency_ms` (documented fallback)."""
    wrapper = _make_wrapper()
    cost_tracker = object.__getattribute__(wrapper, "_cost_tracker")

    await asyncio.sleep(0.010)
    # Deliberately do NOT call `_mark_first_byte`.

    await wrapper._log_request(input_units=1.0)

    create_record_kwargs = cost_tracker.create_record.call_args.kwargs
    ttfb_ms = create_record_kwargs["ttfb_ms"]
    total_ms = create_record_kwargs["total_latency_ms"]

    # Floating-point: the fallback path computes both from the same `now` snapshot
    # so the two values should be exactly equal.
    assert ttfb_ms == total_ms, (
        "When _mark_first_byte is never called, ttfb_ms must fall back to "
        "total_latency_ms. If they diverge, the wrapper introduced a different "
        "fallback path and downstream dashboards may see wrong TTFB values for "
        "non-streaming modalities."
    )


async def test_log_request_is_idempotent() -> None:
    """Calling `_log_request` twice records only once (the wrapper sets `_logged`)."""
    wrapper = _make_wrapper()
    cost_tracker = object.__getattribute__(wrapper, "_cost_tracker")

    await wrapper._log_request(input_units=1.0)
    await wrapper._log_request(input_units=2.0)

    assert cost_tracker.create_record.call_count == 1, (
        "Second `_log_request` call should be a no-op. If both calls record, "
        "the budget enforcer would double-count and storage would have a "
        "duplicate row."
    )
