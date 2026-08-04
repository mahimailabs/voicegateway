"""Async SQLAlchemy engine + session factory used by the ORM layer."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncGenerator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from voicegateway.core.config import GatewayConfig

logger = logging.getLogger(__name__)


DEFAULT_DB_PATH = "~/.config/voicegateway/voicegw.db"


class DatabaseAheadOfCode(RuntimeError):
    """The database is stamped at a revision this build ships no script for.

    Raised when a newer build already migrated the file: a dev checkout and a
    released wheel sharing ``~/.config/voicegateway/voicegw.db`` is the usual
    way in. The schema is then a superset of what this build expects, so reads
    and writes still work and only migrating further is impossible. Callers on
    a telemetry path should log this once and carry on rather than failing the
    host application, which is why it is typed rather than a bare
    ``alembic.util.exc.CommandError``.
    """

    def __init__(self, revision: str, url: str) -> None:
        self.revision = revision
        self.url = url
        super().__init__(
            f"database is stamped at alembic revision {revision!r}, which this "
            f"build of voicegateway does not ship ({url}). It was migrated by a "
            "newer build; upgrade voicegateway or point this process at its own "
            "database with VOICEGW_DB_PATH."
        )


def _unresolvable_revision(exc: Exception) -> str | None:
    """Return the revision alembic could not resolve, if that is why it failed.

    ``upgrade(cfg, "head")`` resolves its target from the scripts on disk, so a
    ``ResolutionError`` underneath is never about the target: it is about the
    revision read out of the database. Matching on the exception chain rather
    than the message keeps this from breaking on an alembic wording change.
    """
    from alembic.script.revision import ResolutionError

    cause: BaseException | None = exc
    seen = 0
    while cause is not None and seen < 5:  # the chain here is 2 deep; bound it anyway
        if isinstance(cause, ResolutionError):
            argument = getattr(cause, "argument", None)
            return argument if isinstance(argument, str) and argument else None
        cause = cause.__cause__
        seen += 1
    return None


# aiosqlite and alembic both log every operation at DEBUG. Embedded in a LiveKit
# agent (voicegateway.attach, or the fleet heartbeat thread), a ``console``/``dev``
# run sets the root logger to DEBUG, so that per-query chatter (every SELECT /
# UPDATE the aiosqlite thread runs) floods the host's terminal. Quiet the two noisy
# dependency loggers, but only when the caller has not picked a level themselves
# (NOTSET = "inherit", which is what produces the flood).
_NOISY_EMBEDDED_LOGGERS = ("aiosqlite", "alembic")


def quiet_embedded_dependency_loggers() -> None:
    """Raise the noisy aiosqlite/alembic loggers to WARNING, unless set already.

    Called from ``Database.__init__`` so it covers every path that builds an engine
    (the cost sink, the dashboard, and the local-mode fleet heartbeat thread that
    constructs a ``Database`` directly), not just the ``StorageService`` facade.
    """
    for name in _NOISY_EMBEDDED_LOGGERS:
        dep_logger = logging.getLogger(name)
        if dep_logger.level == logging.NOTSET:
            dep_logger.setLevel(logging.WARNING)


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


def _enable_sqlite_wal(engine: AsyncEngine) -> None:
    """Put the SQLite file in WAL mode on every new connection.

    WAL lets one writer (the agent's live cost writes) and many readers
    (dashboard queries, and the read-only DuckDB attach in
    :mod:`voicegateway.analytics.duckdb_reader`) proceed concurrently without
    the reader hitting ``database is locked``. ``busy_timeout`` makes any
    residual contention wait briefly rather than erroring immediately. The
    pragma is issued via the sync-engine ``connect`` event, which aiosqlite's
    adapter services synchronously. WAL is a no-op on non-file databases, but
    this is only wired for the SQLite backend.
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
        cursor = dbapi_conn.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
        finally:
            cursor.close()


class Database:
    """Async SQLAlchemy engine + session factory bound to a config."""

    def __init__(self, config: GatewayConfig) -> None:
        quiet_embedded_dependency_loggers()
        self.config = config
        url = resolve_database_url(config)
        self._db_file_path = _resolve_db_file_path(config)
        if url.startswith("sqlite"):
            # Only the SQLite backend has a local file to create. A Postgres
            # collector URL must not touch the filesystem.
            self._db_file_path.parent.mkdir(parents=True, exist_ok=True)
        self._engine = create_async_engine(url, **_engine_kwargs(url))
        if url.startswith("sqlite"):
            _enable_sqlite_wal(self._engine)
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )
        self._migrations_applied = False
        # A migration that failed once will fail the same way every time. Cache
        # it so run_migrations stops re-running alembic; see run_migrations.
        self._migration_error: Exception | None = None
        self._migration_executor: ThreadPoolExecutor | None = None

    @property
    def db_file_path(self) -> Path:
        """The on-disk SQLite path."""
        return self._db_file_path

    @property
    def is_sqlite(self) -> bool:
        """Whether the backend is SQLite (has a local file to attach)."""
        return self._engine.url.get_backend_name() == "sqlite"

    async def create_all(self) -> None:
        """Create every SQLModel-registered table."""
        import voicegateway.models  # noqa: F401

        async with self._engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

    async def dispose(self) -> None:
        """Tear down the connection pool and the migration thread."""
        await self._engine.dispose()
        if self._migration_executor is not None:
            # wait=False: dispose runs on the event loop and a migration still
            # in flight here means the process is already going down.
            self._migration_executor.shutdown(wait=False)
            self._migration_executor = None

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
        """Run ``alembic upgrade head``. Idempotent, and fails at most once.

        Every request-log write goes through
        ``StorageService._ensure_initialized`` -> here, and that flag is only
        set once migrations succeed. So a migration that fails used to be
        retried on every single write: the full alembic upgrade, env.py import
        and connection included, several times a second, each one logging its
        own traceback through the attach sink. The first failure is cached and
        re-raised instead, which costs one dictionary lookup per write.

        The work runs on a private single-thread executor, not
        ``asyncio.to_thread``. ``to_thread`` borrows the event loop's default
        executor, which the loop shuts down during teardown; a write landing
        after that point raised ``RuntimeError: Executor shutdown has been
        called`` instead of doing anything useful. env.py opens its own
        ``asyncio.run``, so this genuinely cannot run on the loop thread.
        """
        if self._migrations_applied:
            return
        if self._migration_error is not None:
            raise self._migration_error
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._executor(), self._run_alembic_upgrade)
        except Exception as exc:
            self._migration_error = exc
            raise
        self._migrations_applied = True

    def _executor(self) -> ThreadPoolExecutor:
        """The private thread migrations run on. Built on first use."""
        if self._migration_executor is None:
            self._migration_executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="voicegw-migrate"
            )
        return self._migration_executor

    def _run_alembic_upgrade(self) -> None:
        from alembic.config import Config
        from alembic.util.exc import CommandError

        from alembic import command

        # Tell env.py to leave the host logging setup alone; see env.py.
        prev = os.environ.get("VOICEGW_ALEMBIC_SKIP_LOGGING")
        os.environ["VOICEGW_ALEMBIC_SKIP_LOGGING"] = "1"
        try:
            cfg = Config(str(_find_alembic_ini()))
            url = resolve_database_url(self.config)
            cfg.set_main_option("sqlalchemy.url", url)
            # env.py computes its own URL by loading voicegw.yaml, which would
            # override the line above and migrate whatever database that file
            # names instead of this one. The attribute tells env.py the caller
            # already knows which database it means: this Database was built
            # from an explicit config, so deferring to a yaml found on disk is
            # never right. Without it, any process that opens a database by
            # path (a test with a tmp file, a second gateway in one process)
            # silently migrates the operator's default DB and leaves its own
            # unmigrated.
            cfg.attributes["voicegw_url"] = url
            try:
                command.upgrade(cfg, "head")
            except CommandError as exc:
                revision = _unresolvable_revision(exc)
                if revision is None:
                    raise
                raise DatabaseAheadOfCode(revision, url) from exc
        finally:
            if prev is None:
                os.environ.pop("VOICEGW_ALEMBIC_SKIP_LOGGING", None)
            else:
                os.environ["VOICEGW_ALEMBIC_SKIP_LOGGING"] = prev
