"""Aiosqlite connection lifecycle for the SQLite storage backend."""

from __future__ import annotations

from pathlib import Path

import aiosqlite


class ConnectionManager:
    """Owns the SQLite file path and tracks open aiosqlite handles."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path).expanduser()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._open_connections: set[aiosqlite.Connection] = set()
        self._initialized = False

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    def mark_initialized(self) -> None:
        self._initialized = True

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


__all__ = ["ConnectionManager"]
