"""Async SQLAlchemy engine + session factory used by the ORM layer."""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
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
    """Compute the SQLAlchemy URL from the gateway config."""
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


class Database:
    """Async SQLAlchemy engine + session factory bound to a config."""

    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        url = resolve_database_url(config)
        self._db_file_path = _resolve_db_file_path(config)
        self._db_file_path.parent.mkdir(parents=True, exist_ok=True)
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
        self._migrations_applied = False

    @property
    def db_file_path(self) -> Path:
        """The on-disk SQLite path (useful for diagnostics + stamping)."""
        return self._db_file_path

    async def create_all(self) -> None:
        """Create every SQLModel-registered table."""
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

    async def run_migrations(self) -> None:
        """Stamp any legacy DB, then ``alembic upgrade head``. Idempotent."""
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
            self._stamp_legacy_db_if_needed(cfg)
            command.upgrade(cfg, "head")
        finally:
            if prev is None:
                os.environ.pop("VOICEGW_ALEMBIC_SKIP_LOGGING", None)
            else:
                os.environ["VOICEGW_ALEMBIC_SKIP_LOGGING"] = prev

    def _stamp_legacy_db_if_needed(self, cfg) -> None:
        """Detect legacy schema state and stamp an alembic_version row."""
        from alembic.script import ScriptDirectory

        from alembic import command

        path = self._db_file_path

        if _has_table(path, "alembic_version"):
            return  # already managed
        if not _has_table(path, "requests"):
            return  # fresh DB — upgrade head builds everything

        # Require the FULL baseline shape before stamping. A partial-legacy
        # DB (test fixtures that seed only ``requests``, for example) falls
        # through; baseline.upgrade() then runs and fills in the missing
        # tables idempotently via CREATE IF NOT EXISTS.
        baseline_required = (
            "sessions",
            "managed_providers",
            "managed_models",
            "managed_projects",
            "config_audit_log",
        )
        if not all(_has_table(path, t) for t in baseline_required):
            return

        detected = _detect_schema_level(path)
        # Only stamp at a revision Alembic actually knows about. The clamp
        # protects against running ahead of the migration tree (during the
        # multi-commit rollout when not every revision is on disk yet).
        script = ScriptDirectory.from_config(cfg)
        known = {rev.revision for rev in script.walk_revisions()}
        if detected not in known:
            head = script.get_current_head() or detected
            detected = head
        command.stamp(cfg, detected)


def _has_table(db_path: Path, table: str) -> bool:
    """True when ``table`` exists in the SQLite file at ``db_path``."""
    if not db_path.exists():
        return False
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
    return row is not None


def _table_columns(db_path: Path, table: str) -> set[str]:
    """Return the set of column names on ``table`` (empty if table missing)."""
    if not db_path.exists():
        return set()
    with sqlite3.connect(str(db_path)) as conn:
        try:
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        except sqlite3.OperationalError:
            return set()
    return {row[1] for row in rows}


def _detect_schema_level(db_path: Path) -> str:
    """Feature-probe the schema and return the matching Alembic revision id.

    Top-down: each level's marker implies all earlier levels are also
    applied, so the first match wins.
    """
    sessions_cols = _table_columns(db_path, "sessions")
    requests_cols = _table_columns(db_path, "requests")
    projects_cols = _table_columns(db_path, "managed_projects")

    if _has_table(db_path, "guardrail_events") or "guardrails_active" in sessions_cols:
        return "0006_guardrails"
    if "routed_llm" in sessions_cols or "branding_json" in projects_cols:
        return "0005_routing_and_branding"
    if "tenant_id" in sessions_cols or "tenant_id" in requests_cols:
        return "0004_tenant_attribution"
    if _has_table(db_path, "replay_stt_events"):
        return "0003_replay_tables"
    if _has_table(db_path, "turns"):
        return "0002_turns_and_deadair"
    return "0001_baseline"
