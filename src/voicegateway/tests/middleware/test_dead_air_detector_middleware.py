"""Contract tests for voicegateway.middleware.dead_air_detector_middleware."""

from __future__ import annotations

import pytest

from voicegateway.middleware.dead_air_detector_middleware import (
    DeadAirDetector,
    DeadAirEvent,
)
from voicegateway.tests.conftest import wait_until


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
    await wait_until(lambda: len(captured) >= 1)
    await detector.stop("s1")

    assert len(captured) >= 1
    event = captured[0]
    assert event.session_id == "s1"
    assert event.threshold_used_ms == 50


async def test_no_rerun_on_continuous_silence() -> None:
    captured: list[DeadAirEvent] = []
    fixed_last_activity = 0  # very old → always over threshold
    polls = 0

    def probe(_: str) -> int:
        nonlocal polls
        polls += 1
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
    await wait_until(lambda: len(captured) >= 1)
    # Count polls, not time: five more full polls of the same silence, each of
    # which would have fired again without the latch.
    fired_at = polls
    await wait_until(lambda: polls >= fired_at + 5)
    await detector.stop("s1")

    # Should fire exactly once for the continuous silence period.
    assert len(captured) == 1


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
    polls = 0

    def probe(_: str) -> int | None:
        nonlocal polls
        polls += 1
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
    # Ten polls span at least 100ms, five times the threshold.
    await wait_until(lambda: polls >= 10)
    await detector.stop("s1")

    assert captured == []
