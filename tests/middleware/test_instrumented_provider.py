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

Post-AC-2-fix: ``InstrumentedSTT`` subclasses ``livekit.agents.stt.STT``
so isinstance gates inside ``agent_activity.py`` succeed. The wrapped
fake therefore needs ``capabilities`` + ``on(event, callback)`` so the
wrapper's ``super().__init__`` and event-bridge construction work.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

from livekit.agents.stt import STTCapabilities

from voicegateway.middleware.instrumented_provider import InstrumentedSTT


def _make_lk_shaped_mock() -> MagicMock:
    """Build a MagicMock pre-loaded with the LK STT-shaped attributes
    the new wrapper consumes during construction.

    Without this, ``super().__init__(capabilities=wrapped.capabilities)``
    would store an auto-generated MagicMock attribute instead of an
    actual ``STTCapabilities``, and any test that ends up reading
    ``self.capabilities.streaming`` from the wrapper would get
    nonsense. The MagicMock's ``on(...)`` is auto-callable and acts as
    a no-op.
    """
    wrapped = MagicMock()
    wrapped.capabilities = STTCapabilities(streaming=False, interim_results=False)
    return wrapped


def _make_wrapper() -> InstrumentedSTT:
    """Build an instrumented STT wrapper with mocked cost tracker + wrapped instance."""
    cost_tracker = MagicMock()
    cost_tracker.create_record = MagicMock(return_value=MagicMock())
    cost_tracker.notify_spend = AsyncMock()
    return InstrumentedSTT(
        wrapped=_make_lk_shaped_mock(),
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


# ---------- proxy + repr + storage path coverage -----------------------


def test_getattr_proxies_to_wrapped_for_unknown_attrs() -> None:
    """Provider-specific attributes (not on lk_stt.STT) still proxy through.

    Post-AC-2-fix the wrapper IS-A ``lk_stt.STT`` so methods declared
    on the LK base class no longer travel via ``__getattr__``. Anything
    *not* defined on the wrapper or the LK hierarchy still falls
    through to the wrapped plugin — that's the contract for
    provider-specific helpers like Cartesia's ``set_voice``.
    """
    wrapped = _make_lk_shaped_mock()
    wrapped.cartesia_specific_helper = MagicMock(return_value="hello")
    wrapped.unique_attribute = 42
    cost_tracker = MagicMock()
    cost_tracker.notify_spend = AsyncMock()
    wrapper = InstrumentedSTT(
        wrapped=wrapped,
        model_id="test/model",
        provider="test",
        project="default",
        cost_tracker=cost_tracker,
        storage=None,
    )
    assert wrapper.cartesia_specific_helper() == "hello"
    assert wrapper.unique_attribute == 42


def test_repr_includes_wrapped_and_modality() -> None:
    """``repr(wrapper)`` exposes both the wrapped instance and the modality."""

    class _FakeSTT:
        def __init__(self) -> None:
            self.capabilities = STTCapabilities(streaming=False, interim_results=False)

        def on(self, event: str, callback: Any) -> None:
            pass

        def __repr__(self) -> str:
            return "FakeSTT()"

    cost_tracker = MagicMock()
    cost_tracker.notify_spend = AsyncMock()
    wrapper = InstrumentedSTT(
        wrapped=_FakeSTT(),
        model_id="test/model",
        provider="test",
        project="default",
        cost_tracker=cost_tracker,
        storage=None,
    )
    rep = repr(wrapper)
    assert "InstrumentedSTT" in rep
    assert "FakeSTT()" in rep


def test_wrapper_is_isinstance_of_lk_stt() -> None:
    """Headline AC-2 contract: ``isinstance(wrapper, lk_stt.STT)`` is True.

    The pre-fix proxy failed every isinstance gate inside
    ``livekit.agents.voice.agent_activity`` (16+ checks), causing the
    ``metrics_collected`` listener to never attach and SpeechHandle's
    5s INTERRUPTION_TIMEOUT to fire on every TTS speech under real
    audio. This is the regression test that catches a future refactor
    reverting that wiring.
    """
    from livekit.agents import stt as lk_stt

    wrapper = _make_wrapper()
    assert isinstance(wrapper, lk_stt.STT), (
        "InstrumentedSTT must subclass livekit.agents.stt.STT so LK's "
        "session bookkeeping (agent_activity._start_session lines 666-678) "
        "registers metrics_collected and error listeners on it. Without "
        "the subclass, every TTS speech fires the 5-second "
        "INTERRUPTION_TIMEOUT in SpeechHandle._cancel and no audio "
        "round-trips."
    )


async def test_log_request_with_storage_writes_to_storage() -> None:
    """Storage path: log_request is called when storage is provided."""
    wrapper = _make_wrapper()
    cost_tracker = object.__getattribute__(wrapper, "_cost_tracker")
    storage = AsyncMock()
    object.__setattr__(wrapper, "_storage", storage)

    await wrapper._log_request(input_units=1.0)

    assert storage.log_request.call_count == 1
    # The record passed to storage is the same one cost_tracker
    # produced; downstream code reads cost_usd from it.
    storage.log_request.assert_called_once_with(cost_tracker.create_record.return_value)


async def test_log_request_swallows_storage_exception() -> None:
    """A failing storage backend must not break in-memory accounting.

    A storage outage already costs the user observability of the
    request; it must not also cause the request itself to fail.
    The wrapper logs at warning and continues to notify the budget
    enforcer so per-project caps still hold.
    """
    wrapper = _make_wrapper()
    cost_tracker = object.__getattribute__(wrapper, "_cost_tracker")

    storage = AsyncMock()
    storage.log_request = AsyncMock(side_effect=RuntimeError("disk full"))
    object.__setattr__(wrapper, "_storage", storage)

    # Must not raise.
    await wrapper._log_request(input_units=1.0)

    # The budget enforcer's cache is still updated despite the
    # storage failure.
    assert cost_tracker.notify_spend.call_count == 1, (
        "notify_spend must run even when storage.log_request raises; "
        "otherwise per-project budget caps drift after any storage "
        "outage."
    )
