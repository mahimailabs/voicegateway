"""Tests for the LK metrics_collected / error -> storage bridge.

The wrapper subscribes to the wrapped plugin's events via ``wrapped.on(...)``;
in tests we hold a MagicMock as the wrapped instance and invoke the bridge
handlers directly (same shape as ``test_ttfb_hook_coverage``). This exercises
the bridge logic end-to-end (extract units -> create_task -> _log_request ->
storage.log_request) without depending on a real LK plugin firing the event.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from voicegateway.middleware.instrumented_provider_middleware import (
    _InstrumentedBase,
    wrap_provider,
)


def _make_wrapper(modality: str) -> _InstrumentedBase:
    """Build an instrumented wrapper around a MagicMock plugin + mock storage."""
    cost_tracker = MagicMock()
    record = SimpleNamespace(
        cost_usd=0.001,
        project="default",
        modality=modality,
        model_id=f"fake/{modality}-model",
    )
    cost_tracker.create_record = MagicMock(return_value=record)
    cost_tracker.notify_spend = AsyncMock()

    storage = MagicMock()
    storage.log_request = AsyncMock()

    wrapper = wrap_provider(
        instance=MagicMock(),
        modality=modality,
        model_id=f"fake/{modality}-model",
        provider="fake",
        project="default",
        cost_tracker=cost_tracker,
        storage=storage,
    )
    assert isinstance(wrapper, _InstrumentedBase)
    return wrapper


async def _drain_pending(wrapper: Any) -> None:
    """Yield the loop so the scheduled coroutine starts, then await any pending."""
    # Two iterations: one for ensure_future to schedule, one for it to advance.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    pending = list(wrapper._pending_log_tasks)
    if pending:
        await asyncio.gather(*pending)


# ---------- success path per modality ---------------------------------------


@pytest.mark.parametrize(
    "modality,metric_kwargs,expected_input,expected_output",
    [
        (
            "llm",
            {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "prompt_cached_tokens": 20,
                "cancelled": False,
            },
            100.0,
            50.0,
        ),
        # 30 seconds of audio expressed as VG's MINUTES convention = 0.5
        ("stt", {"audio_duration": 30.0}, 0.5, 0.0),
        ("tts", {"characters_count": 128, "cancelled": False}, 128.0, 0.0),
    ],
)
async def test_bridge_writes_row_on_success(
    modality: str,
    metric_kwargs: dict[str, Any],
    expected_input: float,
    expected_output: float,
) -> None:
    """metrics_collected event -> storage.log_request with the right units."""
    wrapper = _make_wrapper(modality)
    metric = SimpleNamespace(**metric_kwargs)

    wrapper._on_metrics_log(metric)
    await _drain_pending(wrapper)

    cost_tracker = object.__getattribute__(wrapper, "_cost_tracker")
    storage = object.__getattribute__(wrapper, "_storage")
    assert cost_tracker.create_record.call_count == 1, "exactly one row written"
    assert storage.log_request.call_count == 1

    kwargs = cost_tracker.create_record.call_args.kwargs
    assert kwargs["input_units"] == pytest.approx(expected_input)
    assert kwargs["output_units"] == pytest.approx(expected_output)
    assert kwargs["status"] == "success"
    assert kwargs["model_id"] == f"fake/{modality}-model"
    assert kwargs["modality"] == modality
    assert kwargs["provider"] == "fake"
    # LLM-specific: prompt_cached_tokens on the metric must flow to
    # cached_input_units on the record. STT/TTS metrics carry no cached
    # field so cached_input_units defaults to 0 for those modalities.
    expected_cached = float(metric_kwargs.get("prompt_cached_tokens", 0))
    assert kwargs.get("cached_input_units", 0) == pytest.approx(expected_cached)


# ---------- cancelled path --------------------------------------------------


@pytest.mark.parametrize(
    "modality,metric_kwargs",
    [
        ("llm", {"prompt_tokens": 10, "completion_tokens": 5, "cancelled": True}),
        ("tts", {"characters_count": 20, "cancelled": True}),
    ],
)
async def test_bridge_writes_cancelled_row(
    modality: str, metric_kwargs: dict[str, Any]
) -> None:
    """cancelled=True -> status='cancelled' with partial units preserved."""
    wrapper = _make_wrapper(modality)
    metric = SimpleNamespace(**metric_kwargs)

    wrapper._on_metrics_log(metric)
    await _drain_pending(wrapper)

    cost_tracker = object.__getattribute__(wrapper, "_cost_tracker")
    kwargs = cost_tracker.create_record.call_args.kwargs
    assert kwargs["status"] == "cancelled"
    # Units should still be the partial values LK reports.
    if modality == "llm":
        assert kwargs["input_units"] == pytest.approx(10.0)
        assert kwargs["output_units"] == pytest.approx(5.0)
    elif modality == "tts":
        assert kwargs["input_units"] == pytest.approx(20.0)


# ---------- error path ------------------------------------------------------


async def test_bridge_writes_error_row_on_error_event() -> None:
    """LK 'error' event -> status='error' row with the error message."""
    wrapper = _make_wrapper("llm")

    wrapper._on_error_log(RuntimeError("provider timeout"))
    await _drain_pending(wrapper)

    cost_tracker = object.__getattribute__(wrapper, "_cost_tracker")
    assert cost_tracker.create_record.call_count == 1
    kwargs = cost_tracker.create_record.call_args.kwargs
    assert kwargs["status"] == "error"
    assert "provider timeout" in (kwargs["error_message"] or "")


# ---------- idempotency -----------------------------------------------------


async def test_bridge_does_not_double_log() -> None:
    """metrics_collected then error -> only one row (idempotency via _logged)."""
    wrapper = _make_wrapper("llm")
    metric = SimpleNamespace(prompt_tokens=10, completion_tokens=5, cancelled=False)

    wrapper._on_metrics_log(metric)
    await _drain_pending(wrapper)
    wrapper._on_error_log(RuntimeError("late error"))
    await _drain_pending(wrapper)

    cost_tracker = object.__getattribute__(wrapper, "_cost_tracker")
    assert cost_tracker.create_record.call_count == 1, (
        "second log call must be suppressed by the _logged guard"
    )


# ---------- AgentSession path is preserved ----------------------------------


async def test_bridge_forward_metrics_still_fires() -> None:
    """Wrapper.on('metrics_collected', listener) still receives events.

    Critical: adding the bridge must not break AgentSession's hookup,
    which is what surfaces conversation-level metrics in v0.2.0+.
    """
    wrapper = _make_wrapper("llm")
    seen: list[Any] = []

    wrapper.on("metrics_collected", lambda m: seen.append(m))

    metric = SimpleNamespace(prompt_tokens=10, completion_tokens=5, cancelled=False)
    wrapper._forward_metrics(metric)

    assert seen == [metric], "AgentSession listener must still receive the event"


# ---------- background-task lifecycle ---------------------------------------


async def test_pending_tasks_survive_until_done() -> None:
    """_pending_log_tasks fills on schedule, drains on completion."""
    wrapper = _make_wrapper("llm")
    metric = SimpleNamespace(prompt_tokens=10, completion_tokens=5, cancelled=False)

    wrapper._on_metrics_log(metric)
    # Before any await: the task must already be in the strong-ref set, or
    # Python's GC could collect it mid-flight.
    assert len(wrapper._pending_log_tasks) >= 1, (
        "task was not retained in _pending_log_tasks; GC could collect it"
    )

    await _drain_pending(wrapper)

    # After the task completes the done_callback should have removed it.
    assert len(wrapper._pending_log_tasks) == 0, (
        "done_callback did not discard the completed task"
    )


# ---------- bridge wiring ---------------------------------------------------


async def test_aclose_drains_pending_log_tasks() -> None:
    """aclose() must await any in-flight log writes scheduled by the bridge.

    Without this, a process that exits right after the last call (one-shot
    scripts, end-of-session cleanup) loses the final cost row to
    asyncio.run's pending-task cancellation.
    """
    wrapper = _make_wrapper("llm")
    wrapped = object.__getattribute__(wrapper, "_wrapped")
    wrapped.aclose = AsyncMock()

    metric = SimpleNamespace(prompt_tokens=10, completion_tokens=5, cancelled=False)
    wrapper._on_metrics_log(metric)
    assert len(wrapper._pending_log_tasks) >= 1, "task should be pending pre-aclose"

    await wrapper.aclose()

    assert len(wrapper._pending_log_tasks) == 0, (
        "aclose did not drain the pending log task; rows can be lost on shutdown"
    )
    cost_tracker = object.__getattribute__(wrapper, "_cost_tracker")
    assert cost_tracker.create_record.call_count == 1, (
        "log task should have completed inside aclose, writing the row"
    )


async def test_bridge_handlers_are_registered_on_wrapped() -> None:
    """Sanity: _init_instrumentation actually binds the new handlers."""
    wrapper = _make_wrapper("llm")
    wrapped = object.__getattribute__(wrapper, "_wrapped")

    metrics_handlers = [
        c.args[1] for c in wrapped.on.call_args_list if c.args[0] == "metrics_collected"
    ]
    error_handlers = [
        c.args[1] for c in wrapped.on.call_args_list if c.args[0] == "error"
    ]

    assert wrapper._on_metrics_log in metrics_handlers, (
        "_on_metrics_log not bound to wrapped.on('metrics_collected', ...)"
    )
    assert wrapper._on_error_log in error_handlers, (
        "_on_error_log not bound to wrapped.on('error', ...)"
    )
    # Forward handlers stay too (no regression in AgentSession path).
    assert wrapper._forward_metrics in metrics_handlers
    assert wrapper._forward_error in error_handlers
