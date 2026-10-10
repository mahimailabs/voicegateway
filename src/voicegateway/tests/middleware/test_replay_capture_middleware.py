"""Contract tests for voicegateway.middleware.replay_capture_middleware (T02 of v0.3.0)."""

from __future__ import annotations

import pytest

from voicegateway.middleware.replay_capture_middleware import ReplayCapture, ReplayEvent


async def test_record_state_snapshot_buffers_event() -> None:
    captured: list[list[ReplayEvent]] = []

    async def flush(events: list[ReplayEvent]) -> None:
        captured.append(list(events))

    capture = ReplayCapture(flush_callback=flush, flush_size_events=10)
    await capture.record_state_snapshot(
        {"system_prompt": "you are helpful"}, session_id="s1"
    )
    await capture.close_session("s1")

    assert len(captured) == 1
    [event] = captured[0]
    assert event.modality == "state"
    assert event.session_id == "s1"
    assert event.payload["system_prompt"] == "you are helpful"


async def test_auto_flush_at_threshold() -> None:
    captured: list[list[ReplayEvent]] = []

    async def flush(events: list[ReplayEvent]) -> None:
        captured.append(list(events))

    capture = ReplayCapture(flush_callback=flush, flush_size_events=3)
    for i in range(3):
        await capture.record_state_snapshot({"turn": i}, session_id="s1")

    # Third snapshot hits the threshold and auto-flushes.
    assert len(captured) == 1
    assert len(captured[0]) == 3


async def test_flush_size_must_be_lte_buffer_size() -> None:
    """Pathological config is rejected at construction."""
    with pytest.raises(ValueError):
        ReplayCapture(flush_size_events=100, buffer_size_events=10)


async def test_flush_size_zero_rejected() -> None:
    with pytest.raises(ValueError):
        ReplayCapture(flush_size_events=0)


async def test_session_close_drops_state() -> None:
    capture = ReplayCapture(flush_size_events=10)
    await capture.record_state_snapshot({"text": "x"}, session_id="s1")
    assert "s1" in capture.active_sessions()

    await capture.close_session("s1")
    assert "s1" not in capture.active_sessions()


async def test_flush_callback_failure_reraises() -> None:
    """Callback errors propagate so cost_tracker's session-close path sees them."""

    async def failing_flush(events: list[ReplayEvent]) -> None:
        raise RuntimeError("storage went away")

    capture = ReplayCapture(flush_callback=failing_flush, flush_size_events=10)
    await capture.record_state_snapshot({"text": "x"}, session_id="s1")

    with pytest.raises(RuntimeError, match="storage went away"):
        await capture.close_session("s1")


async def test_multi_session_isolation() -> None:
    captured: list[list[ReplayEvent]] = []

    async def flush(events: list[ReplayEvent]) -> None:
        captured.append(list(events))

    capture = ReplayCapture(flush_callback=flush, flush_size_events=10)
    await capture.record_state_snapshot({"text": "a"}, session_id="sa")
    await capture.record_state_snapshot({"text": "b"}, session_id="sb")

    await capture.close_session("sa")
    await capture.close_session("sb")

    flat = [e for batch in captured for e in batch]
    sessions = {e.session_id for e in flat}
    assert sessions == {"sa", "sb"}
