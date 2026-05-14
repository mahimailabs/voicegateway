"""Hourly retention worker for replay rows."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

from voicegateway.repository import replay_repository as replay

if TYPE_CHECKING:
    from voicegateway.services.storage_service import StorageService

logger = logging.getLogger(__name__)


_DEFAULT_RETENTION_DAYS: Final[int] = 90
_DEFAULT_POLL_INTERVAL_SECONDS: Final[float] = 3600.0  # one hour


RetentionProvider = Callable[[], Awaitable[list[tuple[str, int]]]]


async def _default_provider() -> list[tuple[str, int]]:
    """Empty provider: no projects to retain. Logged at debug level."""
    logger.debug("RetentionWorker no-op provider: no projects configured for retention")
    return []


class RetentionWorker:
    """Background worker that ages out old replay rows per project."""

    def __init__(
        self,
        storage: StorageService,
        retention_provider: RetentionProvider | None = None,
        default_retention_days: int = _DEFAULT_RETENTION_DAYS,
        poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        if default_retention_days < 1:
            raise ValueError(
                f"default_retention_days must be >= 1, got {default_retention_days}"
            )
        if poll_interval_seconds <= 0:
            raise ValueError(
                f"poll_interval_seconds must be > 0, got {poll_interval_seconds}"
            )
        self._storage = storage
        self._provider: RetentionProvider = retention_provider or _default_provider
        self._default_retention_days = default_retention_days
        self._poll_interval = poll_interval_seconds
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Spawn the retention loop. Idempotent."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="replay-retention-worker")

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

    async def tick_now(self) -> dict[str, int]:
        """Run one deletion pass synchronously. Returns per-project counts."""
        return await self._tick()

    # ---- internals -------------------------------------------------------

    async def _loop(self) -> None:
        try:
            while True:
                try:
                    await self._tick()
                except Exception:
                    logger.exception("RetentionWorker tick raised; continuing")
                await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:
            raise

    async def _tick(self) -> dict[str, int]:
        projects = await self._provider()
        if not projects:
            return {}
        from sqlalchemy import text

        per_project_deletes: dict[str, int] = {}
        now = datetime.now(UTC)
        await self._storage._ensure_initialized()
        async with self._storage._conn.session() as db:
            for project_id, retention_days in projects:
                if retention_days < 1:
                    logger.warning(
                        "RetentionWorker: project %s has retention_days=%d "
                        "(< 1); using default %d",
                        project_id,
                        retention_days,
                        self._default_retention_days,
                    )
                    retention_days = self._default_retention_days
                cutoff_iso = (now - timedelta(days=retention_days)).isoformat()
                result = await db.execute(
                    text(
                        "SELECT id FROM sessions "
                        "WHERE project = :project AND ended_at IS NOT NULL "
                        "  AND ended_at < :cutoff"
                    ),
                    {"project": project_id, "cutoff": cutoff_iso},
                )
                stale_ids = [row[0] for row in result]
                if not stale_ids:
                    per_project_deletes[project_id] = 0
                    continue
                deleted_rows = 0
                for sid in stale_ids:
                    deleted_rows += await replay.delete_replay(db, sid)
                per_project_deletes[project_id] = deleted_rows
                logger.info(
                    "RetentionWorker: project %s, retention %d days, "
                    "deleted %d replay rows across %d sessions",
                    project_id,
                    retention_days,
                    deleted_rows,
                    len(stale_ids),
                )
        return per_project_deletes


__all__ = [
    "RetentionProvider",
    "RetentionWorker",
]
