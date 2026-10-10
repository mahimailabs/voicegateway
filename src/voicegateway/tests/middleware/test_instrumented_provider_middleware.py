"""Tests for the _InstrumentedBase TTFB hook contract."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from livekit.agents.stt import STTCapabilities

from voicegateway.middleware.instrumented_provider_middleware import InstrumentedSTT


def _make_lk_shaped_mock() -> MagicMock:
    """Build a MagicMock pre-loaded with the LK STT-shaped attributes"""
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
    """Provider-specific attributes (not on lk_stt.STT) still proxy through."""
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


async def test_log_request_swallows_storage_exception() -> None:
    """A failing storage backend must not break in-memory accounting."""
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
