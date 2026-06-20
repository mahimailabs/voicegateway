"""Async SQLAlchemy engine + session factory used by the ORM layer."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from voicegateway.core.config import GatewayConfig

logger = logging.getLogger(__name__)


DEFAULT_DB_PATH = "~/.config/voicegateway/voicegw.db"


def resolve_database_url(config: GatewayConfig) -> str:
    """Compute the SQLAlchemy URL from the gateway config.

    Precedence: a full ``VOICEGW_DB_URL`` (e.g. a Postgres collector URL)
    wins outright; otherwise the SQLite path (``VOICEGW_DB_PATH`` > config >
    default) is used. This is the seam that lets the same code run embedded on
    SQLite and as a fleet collector on Postgres.
    """
    env_url = os.environ.get("VOICEGW_DB_URL")
    if env_url:
        return env_url
    cost_cfg = config.cost_tracking or {}
    env_db = os.environ.get("VOICEGW_DB_PATH")
    raw_path = env_db or cost_cfg.get("db_path") or DEFAULT_DB_PATH
    path = Path(raw_path).expanduser().resolve()
    return f"sqlite+aiosqlite:///{path}"


def _resolve_db_file_path(config: GatewayConfig) -> Path:
    """Return the on-disk path of the SQLite file the engine targets."""
    cost_cfg = config.cost_tracking or {}
    env_db = os.environ.get("VOICEGW_DB_PATH")
    raw_path = env_db or cost_cfg.get("db_path") or DEFAULT_DB_PATH
    return Path(raw_path).expanduser().resolve()


def _find_alembic_ini() -> Path:
    """Walk up from this file looking for ``alembic.ini`` at the repo root."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "alembic.ini"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"alembic.ini not found above {here}; Database.run_migrations cannot proceed"
    )


def _engine_kwargs(url: str) -> dict[str, Any]:
    """Per-backend ``create_async_engine`` options.

    asyncpg binds each connection to the event loop that created it, and
    ``Gateway.__init__`` runs its async startup through several short-lived
    ``asyncio.run()`` loops (the server later uses its own loop). A pooled
    Postgres connection would then be reused across loops and asyncpg raises
    "got Future attached to a different loop". ``NullPool`` opens a fresh
    connection per checkout, bound to the current loop, which is safe across
    loops (at the cost of no connection pooling). SQLite (aiosqlite) has no such
    constraint and keeps the default pool with pre-ping.
    """
    if url.startswith("sqlite"):
        return {"echo": False, "pool_pre_ping": True}
    return {"echo": False, "poolclass": NullPool}


class Database:
    """Async SQLAlchemy engine + session factory bound to a config."""

    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        url = resolve_database_url(config)
        self._db_file_path = _resolve_db_file_path(config)
        if url.startswith("sqlite"):
            # Only the SQLite backend has a local file to create. A Postgres
            # collector URL must not touch the filesystem.
            self._db_file_path.parent.mkdir(parents=True, exist_ok=True)
        self._engine = create_async_engine(url, **_engine_kwargs(url))
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )
        self._migrations_applied = False

    @property
    def db_file_path(self) -> Path:
        """The on-disk SQLite path."""
        return self._db_file_path

    async def create_all(self) -> None:
        """Create every SQLModel-registered table."""
        import voicegateway.models  # noqa: F401

        async with self._engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

    async def dispose(self) -> None:
        """Tear down the connection pool."""
        await self._engine.dispose()

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """Yield an AsyncSession with rollback-on-error / always-close."""
        async with self._session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    async def run_migrations(self) -> None:
        """Run ``alembic upgrade head``. Idempotent."""
        if self._migrations_applied:
            return
        await asyncio.to_thread(self._run_alembic_upgrade)
        self._migrations_applied = True

    def _run_alembic_upgrade(self) -> None:
        from alembic.config import Config

        from alembic import command

        # Tell env.py to leave the host logging setup alone; see env.py.
        prev = os.environ.get("VOICEGW_ALEMBIC_SKIP_LOGGING")
        os.environ["VOICEGW_ALEMBIC_SKIP_LOGGING"] = "1"
        try:
            cfg = Config(str(_find_alembic_ini()))
            cfg.set_main_option("sqlalchemy.url", resolve_database_url(self.config))
            command.upgrade(cfg, "head")
        finally:
            if prev is None:
                os.environ.pop("VOICEGW_ALEMBIC_SKIP_LOGGING", None)
            else:
                os.environ["VOICEGW_ALEMBIC_SKIP_LOGGING"] = prev
