"""Dead-air detection for voice-conversation metrics."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final

from voicegateway.middleware.base_middleware import PerSessionWatcher

logger = logging.getLogger(__name__)


_DEFAULT_THRESHOLD_SECONDS: Final[float] = 3.0
_DEFAULT_POLL_INTERVAL_SECONDS: Final[float] = 1.0


@dataclass
class DeadAirEvent:
    """One observed silence longer than threshold."""

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


class DeadAirDetector(PerSessionWatcher):
    """Per-session silence watchdog."""

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
        """Cancel the watcher task and drop the session state."""
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

    async def _watch(self, session_id: str) -> None:
        """Inner loop: sleep, probe, decide, emit (or not), repeat."""
        try:
            while True:
                await asyncio.sleep(self._poll_interval)
                last_activity_ms = self._activity_probe(session_id)
                if last_activity_ms is None:
                    continue
                now_ms = int(time.monotonic() * 1000)
                silence_ms = now_ms - last_activity_ms
                if silence_ms < self._threshold_ms:
                    self._already_fired[session_id] = False
                    continue
                if self._already_fired.get(session_id, False):
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
                    logger.exception(
                        "DeadAirDetector on_event raised for session %s",
                        session_id,
                    )
        except asyncio.CancelledError:
            raise


__all__ = [
    "ActivityProbe",
    "DeadAirDetector",
    "DeadAirEvent",
    "EventCallback",
]
