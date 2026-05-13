"""15-minute roll-up worker for v0.5.0 latency_observations.

Implements REQ-VG-ROUTE-002 AC-4 + REQ-VG-ROUTE-003 (Routing
dashboard view freshness). Wakes every 15 minutes, calls
``latency_observations_repo.roll_up`` to recompute the per-
(project, provider, modality) p50/p95 snapshot over the trailing
24-hour window. Single-process for v0.5.0.

Mirrors the v0.3.0 ``RetentionWorker`` pattern (start/stop/tick_now
lifecycle, structured logging on each cycle, asyncio task with
exception-resilient loop). The worker is decoupled from the
ProjectConfig source via the injected ``window_provider`` callable
that returns the trailing-window length in minutes; the wiring
side reads from the live config or hands a static value.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Final

from voicegateway.storage import latency_observations_repo

if TYPE_CHECKING:
    from voicegateway.storage.sqlite import SQLiteStorage

logger = logging.getLogger(__name__)


_DEFAULT_WINDOW_MINUTES: Final[int] = 24 * 60  # OQ2 lock: 24-hour rolling.
_DEFAULT_POLL_INTERVAL_SECONDS: Final[float] = 15 * 60.0  # OQ2 lock: 15-minute refresh.


# Window provider returns the rollup window length in minutes. Async
# to let callers source the value from any backing store (live
# config, future config-watch surface). When omitted, the default
# 24-hour window is used.
WindowProvider = Callable[[], Awaitable[int]]


async def _default_window_provider() -> int:
    return _DEFAULT_WINDOW_MINUTES


class LatencyObservationsWorker:
    """Background worker that refreshes ``latency_observations``.

    Lifecycle: :meth:`start` spawns one asyncio task that loops
    forever, sleeping ``poll_interval_seconds`` between roll-up
    passes. :meth:`stop` cancels the task. :meth:`tick_now` runs
    one pass synchronously and returns the number of inserted
    rollup rows, primarily for test rigs.

    Each pass calls ``latency_observations_repo.roll_up`` with the
    window the provider returns. The repo DELETEs the prior
    snapshot and re-inserts atomically, so the FE never sees a
    half-rolled state.

    Idempotent: re-running with the same data yields the same
    snapshot.
    """

    def __init__(
        self,
        storage: SQLiteStorage,
        window_provider: WindowProvider | None = None,
        poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError(
                f"poll_interval_seconds must be > 0, got {poll_interval_seconds}"
            )
        self._storage = storage
        self._provider: WindowProvider = window_provider or _default_window_provider
        self._poll_interval = poll_interval_seconds
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Spawn the roll-up loop. Idempotent."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(
            self._loop(), name="latency-observations-worker"
        )

    async def stop(self) -> None:
        """Cancel the loop and clear state."""
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def tick_now(self) -> int:
        """Run one roll-up pass synchronously. Returns the inserted row count."""
        return await self._tick()

    # ---- internals -------------------------------------------------------

    async def _loop(self) -> None:
        try:
            while True:
                try:
                    await self._tick()
                except Exception:
                    logger.exception(
                        "LatencyObservationsWorker tick raised; continuing"
                    )
                await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:
            raise

    async def _tick(self) -> int:
        window_minutes = await self._provider()
        if window_minutes < 1:
            logger.warning(
                "LatencyObservationsWorker: window_minutes=%d (< 1); using default %d",
                window_minutes,
                _DEFAULT_WINDOW_MINUTES,
            )
            window_minutes = _DEFAULT_WINDOW_MINUTES
        db = await self._storage._ensure_initialized()
        inserted = await latency_observations_repo.roll_up(
            db, window_minutes=window_minutes
        )
        logger.info(
            "LatencyObservationsWorker: rolled up %d (project, provider, "
            "modality) observations over %d-minute window",
            inserted,
            window_minutes,
        )
        return inserted


__all__ = [
    "LatencyObservationsWorker",
    "WindowProvider",
]
