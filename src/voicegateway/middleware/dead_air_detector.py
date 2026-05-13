"""Dead-air detection for voice-conversation metrics.

Implements REQ-VG-METRICS-004: emit one ``DeadAirEvent`` when both caller
and agent intervals stay silent past the configured threshold (default
3.0 seconds; per-project overridable via ``metrics.dead_air_threshold_seconds``
in T11). One asyncio task per active session polls an injected activity
probe at one-second cadence; re-firing on continuous silence is
suppressed (one event per discrete silence period).

The detector is intentionally repository- and tracker-agnostic. Callers
inject:

- ``activity_probe(session_id) -> int | None``: returns the most recent
  activity timestamp in monotonic milliseconds for the session, or None
  if no activity has been recorded yet. The TurnTracker from T02 will
  implement this in T08's wiring iteration via a small read-only helper;
  for now it is just any callable matching the signature.
- ``on_event(event)``: async callback that persists the event. T08 wires
  this to the dead_air_repo from T06.

The detector itself does not depend on TurnTracker or dead_air_repo; it
is composable so unit tests in T20 can drive it with synthetic probes
and assert event emission timing deterministically.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final

logger = logging.getLogger(__name__)


_DEFAULT_THRESHOLD_SECONDS: Final[float] = 3.0
_DEFAULT_POLL_INTERVAL_SECONDS: Final[float] = 1.0


@dataclass
class DeadAirEvent:
    """One observed silence longer than threshold.

    Maps 1-to-1 to the ``dead_air_events`` table created by storage
    migration 0003 (T04). Timestamps are integer milliseconds on the same
    monotonic clock the TurnTracker uses.
    """

    session_id: str
    started_at_ms: int
    duration_ms: int
    threshold_used_ms: int


ActivityProbe = Callable[[str], int | None]
EventCallback = Callable[[DeadAirEvent], Awaitable[None]]


async def _noop_callback(event: DeadAirEvent) -> None:
    """Default callback used when no repository is wired yet."""
    logger.debug(
        "DeadAirDetector no-op callback: %s dropped (no repository wired)",
        event,
    )


class DeadAirDetector:
    """Per-session silence watchdog.

    :meth:`start` spawns one asyncio task per session. The task wakes
    every ``poll_interval_seconds`` (default 1.0s), calls the activity
    probe, and emits a ``DeadAirEvent`` when the silence has exceeded
    the threshold AND no event has been emitted yet for this silence
    period. When activity resumes (silence drops back below threshold)
    the fired flag resets so the next silence period can trigger again.

    :meth:`stop` cancels the task and drops the session's fired flag.
    Idempotent: starting the same session twice is a no-op; stopping
    an unknown session is a no-op.

    Example::

        detector = DeadAirDetector(
            activity_probe=tracker.last_activity_ms,
            on_event=dead_air_repo.create_event,
            threshold_seconds=project.metrics.dead_air_threshold_seconds,
        )
        await detector.start(session_id)
        # ... session runs ...
        await detector.stop(session_id)
    """

    def __init__(
        self,
        activity_probe: ActivityProbe,
        on_event: EventCallback | None = None,
        threshold_seconds: float = _DEFAULT_THRESHOLD_SECONDS,
        poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        if threshold_seconds <= 0:
            raise ValueError(f"threshold_seconds must be > 0, got {threshold_seconds}")
        if poll_interval_seconds <= 0:
            raise ValueError(
                f"poll_interval_seconds must be > 0, got {poll_interval_seconds}"
            )
        self._activity_probe = activity_probe
        self._on_event: EventCallback = on_event or _noop_callback
        self._threshold_ms = int(threshold_seconds * 1000)
        self._poll_interval = poll_interval_seconds
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._already_fired: dict[str, bool] = {}

    # ---- public lifecycle ------------------------------------------------

    async def start(self, session_id: str) -> None:
        """Spawn the watcher task for this session. Idempotent."""
        if session_id in self._tasks:
            return
        self._already_fired[session_id] = False
        self._tasks[session_id] = asyncio.create_task(
            self._watch(session_id),
            name=f"dead-air-{session_id}",
        )

    async def stop(self, session_id: str) -> None:
        """Cancel the watcher task and drop the session state.

        Awaits the task's cancellation so subsequent state checks see
        the task as fully torn down. No-op when the session is unknown.
        """
        task = self._tasks.pop(session_id, None)
        self._already_fired.pop(session_id, None)
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            # Expected. swallow.
            pass

    def active_sessions(self) -> list[str]:
        """List the session ids with running watcher tasks. Diagnostics only."""
        return list(self._tasks.keys())

    # ---- internals -------------------------------------------------------

    async def _watch(self, session_id: str) -> None:
        """Inner loop: sleep, probe, decide, emit (or not), repeat."""
        try:
            while True:
                await asyncio.sleep(self._poll_interval)
                last_activity_ms = self._activity_probe(session_id)
                if last_activity_ms is None:
                    # No activity baseline yet; cannot measure silence.
                    continue
                now_ms = int(time.monotonic() * 1000)
                silence_ms = now_ms - last_activity_ms
                if silence_ms < self._threshold_ms:
                    self._already_fired[session_id] = False
                    continue
                if self._already_fired.get(session_id, False):
                    # One event per silence period; suppress re-firing.
                    continue
                event = DeadAirEvent(
                    session_id=session_id,
                    started_at_ms=last_activity_ms,
                    duration_ms=silence_ms,
                    threshold_used_ms=self._threshold_ms,
                )
                self._already_fired[session_id] = True
                try:
                    await self._on_event(event)
                except Exception:
                    # Do not crash the watcher on callback failure; the
                    # event was best-effort observability. Reset the
                    # fired flag so a future activity-then-silence cycle
                    # still emits.
                    logger.exception(
                        "DeadAirDetector on_event raised for session %s",
                        session_id,
                    )
        except asyncio.CancelledError:
            # Expected cancellation path from stop(); re-raise so the
            # caller's await sees it.
            raise


__all__ = [
    "ActivityProbe",
    "DeadAirDetector",
    "DeadAirEvent",
    "EventCallback",
]
