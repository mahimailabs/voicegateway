"""Hourly retention worker: ages out requests, sessions, calls, and their rows.

Per project, sessions older than the cutoff (by ``ended_at``) and their
dependent rows (replay, turns, dead-air) are deleted child-first; calls and
their legs prune on their own clock (a call can exist with no session at all);
diagnostics runs prune on their own ISO-8601 timestamps; node samples prune on
theirs (and are also trimmed unconditionally by the scrape worker, which is what
actually bounds that table); requests are pruned
independently by ``timestamp`` (a request may have no session). Deletes are hard
and batched so a large backlog does not hold a long write lock, which keeps the
pass friendly to both SQLite and Postgres.
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
    "transcript_turns",
)
# Children of a ``calls`` row, deleted before it. Calls prune on their own clock
# (epoch milliseconds), not the session's: a call can exist with no session at
# all, which is the whole reason the table exists.
_CALL_CHILD_TABLES: Final[tuple[str, ...]] = ("call_legs",)


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

    Sessions and their dependent rows (replay, turns, dead-air) prune
    by ``ended_at``; calls and their legs prune by their own epoch-millisecond
    timestamps; diagnostics runs prune by their ISO-8601 timestamps; node
    samples prune by ``at_ms``; requests
    prune independently by ``timestamp``. Deletes run child-first in batches on a
    periodic loop.
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

        Children first (replay, turns, dead-air), then the session rows, then
        calls with their legs, then diagnostics runs, then node samples, then
        requests independently
        by timestamp. Each chunk commits so a large backlog never holds one long
        write lock.

        A request is NOT a child of a session (it prunes on the request clock
        and routinely outlives the session it names), so it is not deleted with
        the session pass; its ``session_id`` pointer is nulled instead, in the
        same chunk transaction as the delete. That keeps the invariant at the
        source, which is the only guard this pointer has: no read joins
        ``requests`` to ``sessions``, so unlike ``sessions.call_id`` there is no
        read-time LEFT JOIN that would filter a dangling one out.
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
            # A request is NOT a child of a session (it prunes on its own
            # ``timestamp`` clock and routinely outlives the session it names),
            # so it is not deleted here; its ``session_id`` pointer is dropped
            # before the row it points at, inside this chunk's transaction, so
            # a crash mid-sweep can never commit the delete without the
            # null-out. Deliberately not scoped by project: a pointer from
            # anywhere at a session that is going away is dangling either way.
            # Not added to ``deleted`` either, which counts deleted rows and
            # would otherwise over-report.
            await db.execute(
                text(
                    "UPDATE requests SET session_id = NULL WHERE session_id IN :ids"
                ).bindparams(bindparam("ids", expanding=True)),
                {"ids": ids},
            )
            res = await db.execute(
                text("DELETE FROM sessions WHERE id IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                ),
                {"ids": ids},
            )
            deleted += _rowcount(res)
            await db.commit()

        deleted += await self._prune_calls(db, project_id, cutoff_ts)
        deleted += await self._prune_diagnostics_runs(db, project_id, cutoff_iso)
        deleted += await self._prune_node_samples(db, project_id, cutoff_ts)
        deleted += await self._prune_load_runs(db, project_id, cutoff_ts)

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

    async def _prune_calls(
        self, db: AsyncSession, project_id: str, cutoff_ts: float
    ) -> int:
        """Hard-delete one project's aged calls + their legs; return the count.

        Calls are their own root: a call can exist with no session (that is why
        the table exists), so it prunes by its own timestamps rather than with
        the session pass above. Age is taken from ``ended_at_ms`` when the call
        ended and ``started_at_ms`` otherwise, so a call whose end webhook never
        arrived still ages out instead of living forever. A row with neither
        timestamp has no age and is left alone.

        The cutoff arrives in epoch seconds and this table stores epoch
        milliseconds, hence the conversion here.

        A session is NOT a child of a call (it prunes on the session clock and
        routinely outlives the call it names), so it is not deleted here; its
        ``call_id`` pointer is nulled instead, in the same chunk transaction as
        the delete. That keeps the invariant at the source. The read side keeps
        its own LEFT JOIN guard against dangling pointers regardless: this is
        defence in depth, not a replacement for it.
        """
        deleted = 0
        cutoff_ms = int(cutoff_ts * 1000)
        result = await db.execute(
            text(
                "SELECT id FROM calls "
                "WHERE project = :project "
                "  AND COALESCE(ended_at_ms, started_at_ms) IS NOT NULL "
                "  AND COALESCE(ended_at_ms, started_at_ms) < :cutoff_ms"
            ),
            {"project": project_id, "cutoff_ms": cutoff_ms},
        )
        stale_ids = [row[0] for row in result]
        for chunk in _chunked(stale_ids, self._batch_size):
            ids = list(chunk)
            for table in _CALL_CHILD_TABLES:
                res = await db.execute(
                    text(f"DELETE FROM {table} WHERE call_id IN :ids").bindparams(
                        bindparam("ids", expanding=True)
                    ),
                    {"ids": ids},
                )
                deleted += _rowcount(res)
            # Drop the pointers before the rows they point at, inside this
            # chunk's transaction, so a crash mid-sweep can never commit the
            # delete without the null-out. Deliberately not scoped by project:
            # a pointer from anywhere at a call that is going away is dangling
            # either way. Not added to ``deleted`` either, which counts deleted
            # rows and would otherwise over-report.
            await db.execute(
                text(
                    "UPDATE sessions SET call_id = NULL WHERE call_id IN :ids"
                ).bindparams(bindparam("ids", expanding=True)),
                {"ids": ids},
            )
            res = await db.execute(
                text("DELETE FROM calls WHERE id IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                ),
                {"ids": ids},
            )
            deleted += _rowcount(res)
            await db.commit()
        return deleted

    async def _prune_diagnostics_runs(
        self, db: AsyncSession, project_id: str, cutoff_iso: str
    ) -> int:
        """Hard-delete one project's aged diagnostics runs; return the count.

        A run has no children, so there is nothing to delete first. It ages on
        ``ended_at`` when it ended and ``created_at`` otherwise, so a run whose
        process died mid-flight still ages out instead of sitting in the history
        as "running" forever.

        The comparison is lexical on ISO-8601 strings, which is exactly what the
        session pass above does with ``sessions.ended_at``: every writer formats
        with ``datetime.now(UTC).isoformat()``, so the strings are same-format
        UTC and sort chronologically.
        """
        deleted = 0
        while True:
            res = await db.execute(
                text(
                    "DELETE FROM diagnostics_runs WHERE run_id IN ("
                    "  SELECT run_id FROM diagnostics_runs "
                    "  WHERE project = :project "
                    # created_at is NOT NULL, so unlike the calls pass this
                    # COALESCE always yields a value: every run has an age.
                    "    AND COALESCE(ended_at, created_at) < :cutoff "
                    "  LIMIT :limit)"
                ),
                {
                    "project": project_id,
                    "cutoff": cutoff_iso,
                    "limit": self._batch_size,
                },
            )
            removed = _rowcount(res)
            await db.commit()
            deleted += removed
            if removed == 0:
                break
        return deleted

    async def _prune_node_samples(
        self, db: AsyncSession, project_id: str, cutoff_ts: float
    ) -> int:
        """Hard-delete one project's aged node samples; return the count.

        A sample has no children and no parent: layer 7 is correlated by
        ``(node, time window)``, never by a foreign key to a call, so this
        prunes on its own epoch-millisecond clock exactly like the calls pass.
        Every row has an age (``at_ms`` is NOT NULL), so unlike calls there is
        no "no timestamp, leave it alone" case.

        This is the SECOND line of defence, not the first. Scraping ~10 nodes at
        15 s is ~57k rows/day, and this pass is opt-in (``retention.enabled``)
        and only ever sees projects discovered from ``requests``/``sessions`` --
        which a deployment that scrapes nodes but runs no inference never
        populates. The scrape worker therefore trims the table itself on every
        tick; this pass exists so samples also age out on the operator's
        configured clock, and so a project's samples go when the project's data
        goes.
        """
        deleted = 0
        cutoff_ms = int(cutoff_ts * 1000)
        while True:
            res = await db.execute(
                text(
                    "DELETE FROM node_samples WHERE id IN ("
                    "  SELECT id FROM node_samples "
                    "  WHERE project = :project AND at_ms < :cutoff_ms "
                    "  LIMIT :limit)"
                ),
                {
                    "project": project_id,
                    "cutoff_ms": cutoff_ms,
                    "limit": self._batch_size,
                },
            )
            removed = _rowcount(res)
            await db.commit()
            deleted += removed
            if removed == 0:
                break
        return deleted

    async def _prune_load_runs(
        self, db: AsyncSession, project_id: str, cutoff_ts: float
    ) -> int:
        """Hard-delete one project's aged load runs + their tests; return count.

        A load run is its own root: it is produced by an external generator and
        has no session or call to hang from, so it prunes on its own
        epoch-millisecond clock like the calls and node-samples passes.

        Ages on ``ended_at_ms`` when the run ended and ``created_at_ms``
        otherwise, so a run whose import died mid-flight still ages out instead
        of sitting in the history forever. ``created_at_ms`` is NOT NULL, so
        every row has an age and there is no leave-it-alone case.

        The child rows go FIRST, inside the same chunk transaction as the
        parent. A crash between the two would otherwise leave ``load_run_tests``
        rows pointing at a run that no longer exists, and nothing reads those
        back to a user, so they would accumulate unseen.
        """
        deleted = 0
        cutoff_ms = int(cutoff_ts * 1000)
        while True:
            result = await db.execute(
                text(
                    "SELECT id FROM load_runs "
                    "WHERE project = :project "
                    "  AND COALESCE(ended_at_ms, created_at_ms) < :cutoff_ms "
                    "LIMIT :limit"
                ),
                {
                    "project": project_id,
                    "cutoff_ms": cutoff_ms,
                    "limit": self._batch_size,
                },
            )
            ids = [row[0] for row in result]
            if not ids:
                break
            res = await db.execute(
                text("DELETE FROM load_run_tests WHERE run_id IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                ),
                {"ids": ids},
            )
            deleted += _rowcount(res)
            res = await db.execute(
                text("DELETE FROM load_runs WHERE id IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                ),
                {"ids": ids},
            )
            deleted += _rowcount(res)
            await db.commit()
        return deleted


__all__ = [
    "RetentionProvider",
    "RetentionWorker",
]
