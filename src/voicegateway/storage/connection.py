"""Aiosqlite connection lifecycle for the SQLite storage backend."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from voicegateway.core.database import Database


class ConnectionManager:
    """Owns the SQLite file path and tracks open aiosqlite handles.

    Also lazily owns a :class:`Database` so services can call
    :meth:`session` to get an ORM ``AsyncSession`` over the same file.
    The two paths coexist on one SQLite file via file-level locking.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path).expanduser()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._open_connections: set[aiosqlite.Connection] = set()
        self._initialized = False
        self._database: Database | None = None

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    def mark_initialized(self) -> None:
        self._initialized = True

    def _ensure_database(self) -> Database:
        if self._database is None:
            from voicegateway.core.config import GatewayConfig
            from voicegateway.core.database import Database

            cfg = GatewayConfig(cost_tracking={"db_path": str(self._db_path)})
            self._database = Database(cfg)
        return self._database

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield an ORM ``AsyncSession`` (commits on exit; rollback on raise)."""
        db = self._ensure_database()
        async with db.session() as s:
            yield s

    async def connect(self) -> aiosqlite.Connection:
        """Open a tracked aiosqlite connection."""
        db = await aiosqlite.connect(str(self._db_path))
        self._track(db)
        return db

    def _track(self, db: aiosqlite.Connection) -> None:
        """Wrap db.close so closed handles drop out of the tracking set."""
        self._open_connections.add(db)
        original_close = db.close

        async def _tracked_close() -> None:
            try:
                await original_close()
            finally:
                self._open_connections.discard(db)

        db.close = _tracked_close  # type: ignore[method-assign]

    async def aclose(self) -> None:
        """Close every raw handle still owned by this manager."""
        for db in list(self._open_connections):
            try:
                await db.close()
            except Exception:
                self._open_connections.discard(db)
        if self._database is not None:
            await self._database.dispose()
            self._database = None


__all__ = ["ConnectionManager"]
