"""Hourly retention worker: ages out requests, sessions, and their rows.

Per project, sessions older than the cutoff (by ``ended_at``) and their
dependent rows (replay, turns, dead-air, guardrail) are deleted child-first;
requests are pruned independently by ``timestamp`` (a request may have no
session). Deletes are hard and batched so a large backlog does not hold a
long write lock, which keeps the pass friendly to both SQLite and Postgres.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import bindparam, text

from voicegateway.repository import replay_repository as replay

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from voicegateway.services.storage_service import StorageService

logger = logging.getLogger(__name__)


_DEFAULT_RETENTION_DAYS: Final[int] = 90
_DEFAULT_POLL_INTERVAL_SECONDS: Final[float] = 3600.0  # one hour
_DEFAULT_BATCH_SIZE: Final[int] = 500
_SESSION_CHILD_TABLES: Final[tuple[str, ...]] = (
    "turns",
    "dead_air_events",
    "guardrail_events",
)


def _rowcount(result: Any) -> int:
    """A DELETE's affected-row count (CursorResult.rowcount), 0 if unknown."""
    return result.rowcount or 0


def _chunked(items: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    """Yield ``items`` in contiguous chunks of at most ``size``."""
    for start in range(0, len(items), size):
        yield items[start : start + size]


RetentionProvider = Callable[[], Awaitable[list[tuple[str, int]]]]


async def _default_provider() -> list[tuple[str, int]]:
    """Empty provider: no projects to retain. Logged at debug level."""
    logger.debug("RetentionWorker no-op provider: no projects configured for retention")
    return []


class RetentionWorker:
    """Background worker that hard-deletes aged rows per project.

    Sessions and their dependent rows (replay, turns, dead-air, guardrail) prune
    by ``ended_at``; requests prune independently by ``timestamp``. Deletes run
    child-first in batches on a periodic loop.
    """

    def __init__(
        self,
        storage: StorageService,
        retention_provider: RetentionProvider | None = None,
        default_retention_days: int = _DEFAULT_RETENTION_DAYS,
        poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
        batch_size: int = _DEFAULT_BATCH_SIZE,
    ) -> None:
        if default_retention_days < 1:
            raise ValueError(
                f"default_retention_days must be >= 1, got {default_retention_days}"
            )
        if poll_interval_seconds <= 0:
            raise ValueError(
                f"poll_interval_seconds must be > 0, got {poll_interval_seconds}"
            )
        if batch_size < 1:
            raise ValueError(f"batch_size must be >= 1, got {batch_size}")
        self._storage = storage
        self._provider: RetentionProvider = retention_provider or _default_provider
        self._default_retention_days = default_retention_days
        self._poll_interval = poll_interval_seconds
        self._batch_size = batch_size
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
                cutoff = now - timedelta(days=retention_days)
                deleted = await self._prune_project(
                    db, project_id, cutoff.isoformat(), cutoff.timestamp()
                )
                per_project_deletes[project_id] = deleted
                logger.info(
                    "RetentionWorker: project %s, retention %d days, deleted %d row(s)",
                    project_id,
                    retention_days,
                    deleted,
                )
        return per_project_deletes

    async def _prune_project(
        self,
        db: AsyncSession,
        project_id: str,
        cutoff_iso: str,
        cutoff_ts: float,
    ) -> int:
        """Hard-delete one project's aged rows; return the total row count.

        Children first (replay, turns, dead-air, guardrail), then the session
        rows, then requests independently by timestamp. Each chunk commits so a
        large backlog never holds one long write lock.
        """
        deleted = 0

        result = await db.execute(
            text(
                "SELECT id FROM sessions "
                "WHERE project = :project AND ended_at IS NOT NULL "
                "  AND ended_at < :cutoff"
            ),
            {"project": project_id, "cutoff": cutoff_iso},
        )
        stale_ids = [row[0] for row in result]
        for chunk in _chunked(stale_ids, self._batch_size):
            ids = list(chunk)
            for sid in ids:
                deleted += await replay.delete_replay(db, sid)
            for table in _SESSION_CHILD_TABLES:
                res = await db.execute(
                    text(f"DELETE FROM {table} WHERE session_id IN :ids").bindparams(
                        bindparam("ids", expanding=True)
                    ),
                    {"ids": ids},
                )
                deleted += _rowcount(res)
            res = await db.execute(
                text("DELETE FROM sessions WHERE id IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                ),
                {"ids": ids},
            )
            deleted += _rowcount(res)
            await db.commit()

        # Requests prune on their own clock (a request may have no session_id).
        while True:
            res = await db.execute(
                text(
                    "DELETE FROM requests WHERE id IN ("
                    "  SELECT id FROM requests "
                    "  WHERE project = :project AND timestamp < :cutoff "
                    "  LIMIT :limit)"
                ),
                {
                    "project": project_id,
                    "cutoff": cutoff_ts,
                    "limit": self._batch_size,
                },
            )
            removed = _rowcount(res)
            await db.commit()
            deleted += removed
            if removed == 0:
                break

        return deleted


__all__ = [
    "RetentionProvider",
    "RetentionWorker",
]
