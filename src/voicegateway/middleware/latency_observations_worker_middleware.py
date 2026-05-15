"""15-minute roll-up worker for the ``latency_observations`` table."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Final

from voicegateway.repository import (
    latency_observations_repository as latency_observations,
)

if TYPE_CHECKING:
    from voicegateway.services.storage_service import StorageService

logger = logging.getLogger(__name__)


_DEFAULT_WINDOW_MINUTES: Final[int] = 24 * 60
_DEFAULT_POLL_INTERVAL_SECONDS: Final[float] = 15 * 60.0


WindowProvider = Callable[[], Awaitable[int]]


async def _default_window_provider() -> int:
    return _DEFAULT_WINDOW_MINUTES


class LatencyObservationsWorker:
    """Background worker that refreshes ``latency_observations``."""

    def __init__(
        self,
        storage: StorageService,
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
        await self._storage._ensure_initialized()
        async with self._storage._conn.session() as db:
            inserted = await latency_observations.roll_up(
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
