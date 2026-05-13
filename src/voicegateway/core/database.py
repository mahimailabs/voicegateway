"""Async SQLAlchemy engine + session factory used by the ORM layer.

Wraps :func:`sqlalchemy.ext.asyncio.create_async_engine` and an
``async_sessionmaker`` behind a single :class:`Database` object. Repos
inject ``database.session`` (a context-managed factory) so they never
manage engine lifecycle themselves.

The existing raw-aiosqlite paths in :mod:`voicegateway.storage.sqlite`
remain untouched: this engine is dormant until a repository or service
opts in by importing it.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel

from voicegateway.core.config import GatewayConfig

logger = logging.getLogger(__name__)


DEFAULT_DB_PATH = "~/.config/voicegateway/voicegw.db"


def resolve_database_url(config: GatewayConfig) -> str:
    """Compute the SQLAlchemy URL from the gateway config.

    Order of precedence (matches the legacy aiosqlite path):

    1. ``VOICEGW_DB_PATH`` environment variable (file path).
    2. ``cost_tracking.db_path`` in voicegw.yaml.
    3. :data:`DEFAULT_DB_PATH`.

    Always rendered as ``sqlite+aiosqlite:///<absolute path>``. When the
    parent directory does not exist it is created on first use.
    """
    cost_cfg = config.cost_tracking or {}
    env_db = os.environ.get("VOICEGW_DB_PATH")
    raw_path = env_db or cost_cfg.get("db_path") or DEFAULT_DB_PATH
    path = Path(raw_path).expanduser().resolve()
    return f"sqlite+aiosqlite:///{path}"


class Database:
    """Async SQLAlchemy engine + session factory bound to a config."""

    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        url = resolve_database_url(config)
        self._engine = create_async_engine(
            url,
            echo=False,
            pool_pre_ping=True,
        )
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )

    async def create_all(self) -> None:
        """Create every SQLModel-registered table.

        Reserved for the test bootstrap. Production schema changes go
        through Alembic.
        """
        # Importing here ensures every model module is loaded before
        # ``SQLModel.metadata`` is read, the same pattern Alembic uses.
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
