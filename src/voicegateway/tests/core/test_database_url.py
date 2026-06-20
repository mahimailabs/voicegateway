"""Tests for VOICEGW_DB_URL dual-dialect resolution in core/database.py."""

from __future__ import annotations

import pytest
from sqlalchemy.pool import NullPool

from voicegateway.core.config import GatewayConfig
from voicegateway.core.database import Database, _engine_kwargs, resolve_database_url


def test_resolve_database_url_honors_db_url_env(monkeypatch):
    """A full VOICEGW_DB_URL takes precedence over the SQLite path."""
    monkeypatch.setenv("VOICEGW_DB_URL", "postgresql+asyncpg://u:p@localhost/vg")
    url = resolve_database_url(GatewayConfig(cost_tracking={"db_path": "/tmp/x.db"}))
    assert url == "postgresql+asyncpg://u:p@localhost/vg"


def test_resolve_database_url_falls_back_to_sqlite_path(monkeypatch, tmp_path):
    """Without VOICEGW_DB_URL the SQLite path is used."""
    monkeypatch.delenv("VOICEGW_DB_URL", raising=False)
    monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)
    db_path = tmp_path / "x.db"
    url = resolve_database_url(GatewayConfig(cost_tracking={"db_path": str(db_path)}))
    assert url.startswith("sqlite+aiosqlite:///")
    assert str(db_path) in url


def test_database_builds_postgres_engine_for_db_url(monkeypatch):
    """A Postgres URL produces a postgres engine (and no filesystem mkdir).

    Building the engine imports the asyncpg driver, so this skips when the
    optional [postgres] extra is not installed (e.g. the default CI [dev] env).
    """
    pytest.importorskip("asyncpg")
    monkeypatch.setenv("VOICEGW_DB_URL", "postgresql+asyncpg://u:p@localhost/vg")
    db = Database(GatewayConfig())
    assert db._engine.url.get_backend_name() == "postgresql"
    # asyncpg connections are loop-bound; the engine must not pool them.
    assert isinstance(db._engine.pool, NullPool)


def test_engine_kwargs_postgres_uses_nullpool():
    """asyncpg binds connections to their creating loop, so the Postgres engine
    must use NullPool and not reuse a connection across Gateway.__init__'s
    short-lived asyncio.run() loops."""
    kwargs = _engine_kwargs("postgresql+asyncpg://u:p@localhost/vg")
    assert kwargs["poolclass"] is NullPool


def test_engine_kwargs_sqlite_keeps_default_pool():
    """SQLite (aiosqlite) tolerates cross-loop reuse, so it keeps the default
    pool with pre-ping rather than switching to NullPool."""
    kwargs = _engine_kwargs("sqlite+aiosqlite:////tmp/x.db")
    assert "poolclass" not in kwargs
    assert kwargs["pool_pre_ping"] is True
