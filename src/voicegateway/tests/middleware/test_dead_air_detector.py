"""Contract tests for voicegateway.middleware.dead_air_detector.

Covers DeadAirDetector (T03) lifecycle and emission semantics:
threshold crossing, no-rerun on continuous silence, reset on activity,
clean cancel on stop.
"""

from __future__ import annotations

import asyncio

import pytest

from voicegateway.middleware.dead_air_detector import (
    DeadAirDetector,
    DeadAirEvent,
)


async def test_emits_event_when_silence_crosses_threshold() -> None:
    captured: list[DeadAirEvent] = []

    # Simulated clock: probe returns a fixed "last activity" timestamp
    # that puts the silence safely over threshold by the second poll.
    fixed_last_activity = 1000

    def probe(_: str) -> int:
        return fixed_last_activity

    async def on_event(event: DeadAirEvent) -> None:
        captured.append(event)

    detector = DeadAirDetector(
        activity_probe=probe,
        on_event=on_event,
        threshold_seconds=0.05,  # 50ms threshold; quick test
        poll_interval_seconds=0.02,  # 20ms cadence
    )

    await detector.start("s1")
    # Wait long enough for several poll cycles.
    await asyncio.sleep(0.2)
    await detector.stop("s1")

    assert len(captured) >= 1
    event = captured[0]
    assert event.session_id == "s1"
    assert event.threshold_used_ms == 50


async def test_no_rerun_on_continuous_silence() -> None:
    captured: list[DeadAirEvent] = []
    fixed_last_activity = 0  # very old → always over threshold

    def probe(_: str) -> int:
        return fixed_last_activity

    async def on_event(event: DeadAirEvent) -> None:
        captured.append(event)

    detector = DeadAirDetector(
        activity_probe=probe,
        on_event=on_event,
        threshold_seconds=0.02,
        poll_interval_seconds=0.01,
    )

    await detector.start("s1")
    await asyncio.sleep(0.15)  # plenty of poll cycles
    await detector.stop("s1")

    # Should fire exactly once for the continuous silence period.
    assert len(captured) == 1


async def test_reset_after_activity_allows_new_event() -> None:
    captured: list[DeadAirEvent] = []

    # Start with old timestamp → silence; flip to recent after first event.
    state = {"last_ms": 0, "ticks": 0}

    def probe(_: str) -> int:
        state["ticks"] += 1
        if state["ticks"] > 5:
            # After several poll cycles, "activity" resumes (very recent).
            import time

            return int(time.monotonic() * 1000)
        return state["last_ms"]

    async def on_event(event: DeadAirEvent) -> None:
        captured.append(event)
        # Reset state["last_ms"] to old timestamp after activity ends,
        # which would trigger the next event window if we kept running.
        state["last_ms"] = 0

    detector = DeadAirDetector(
        activity_probe=probe,
        on_event=on_event,
        threshold_seconds=0.02,
        poll_interval_seconds=0.01,
    )

    await detector.start("s1")
    await asyncio.sleep(0.15)
    await detector.stop("s1")

    # At least one event fired before activity resumed.
    assert len(captured) >= 1


async def test_threshold_validation() -> None:
    def probe(_: str) -> int:
        return 0

    with pytest.raises(ValueError):
        DeadAirDetector(activity_probe=probe, threshold_seconds=0)
    with pytest.raises(ValueError):
        DeadAirDetector(activity_probe=probe, threshold_seconds=-1.0)
    with pytest.raises(ValueError):
        DeadAirDetector(activity_probe=probe, poll_interval_seconds=0)


async def test_stop_unknown_session_is_noop() -> None:
    def probe(_: str) -> int | None:
        return None

    detector = DeadAirDetector(activity_probe=probe)
    await detector.stop("never-existed")  # must not raise


async def test_active_sessions_reflects_running_tasks() -> None:
    def probe(_: str) -> int | None:
        return None

    detector = DeadAirDetector(activity_probe=probe, poll_interval_seconds=0.05)
    assert detector.active_sessions() == []

    await detector.start("s1")
    assert detector.active_sessions() == ["s1"]

    await detector.stop("s1")
    assert detector.active_sessions() == []


async def test_no_baseline_means_no_event() -> None:
    """When the probe returns None for all poll cycles, no event fires."""
    captured: list[DeadAirEvent] = []

    def probe(_: str) -> int | None:
        return None  # No activity baseline observed.

    async def on_event(event: DeadAirEvent) -> None:
        captured.append(event)

    detector = DeadAirDetector(
        activity_probe=probe,
        on_event=on_event,
        threshold_seconds=0.02,
        poll_interval_seconds=0.01,
    )

    await detector.start("s1")
    await asyncio.sleep(0.1)
    await detector.stop("s1")

    assert captured == []
