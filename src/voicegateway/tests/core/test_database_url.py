"""Tests for VOICEGW_DB_URL dual-dialect resolution in core/database.py."""

from __future__ import annotations

from voicegateway.core.config import GatewayConfig
from voicegateway.core.database import Database, resolve_database_url


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
    """A Postgres URL produces a postgres engine (and no filesystem mkdir)."""
    monkeypatch.setenv("VOICEGW_DB_URL", "postgresql+asyncpg://u:p@localhost/vg")
    db = Database(GatewayConfig())
    assert db._engine.url.get_backend_name() == "postgresql"
