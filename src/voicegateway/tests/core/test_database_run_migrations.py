"""End-to-end check: Database.run_migrations against a fresh SQLite file."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from voicegateway.core.config import GatewayConfig
from voicegateway.core.database import Database, _find_alembic_ini


def _build_config(db_path: Path) -> GatewayConfig:
    return GatewayConfig(cost_tracking={"db_path": str(db_path)})


def _current_head() -> str:
    cfg = Config(str(_find_alembic_ini()))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    assert head is not None
    return head


@pytest.mark.asyncio
async def test_run_migrations_on_fresh_db_builds_baseline(tmp_path: Path) -> None:
    db_path = tmp_path / "voicegw.db"
    db = Database(_build_config(db_path))
    try:
        await db.run_migrations()
    finally:
        await db.dispose()

    with sqlite3.connect(str(db_path)) as conn:
        # alembic_version row pinned at the current head.
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        assert version is not None
        assert version[0] == _current_head()

        # Every table exists.
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    expected = {
        "agent_observations",
        "alembic_version",
        "config_audit_log",
        "dead_air_events",
        "guardrail_events",
        "latency_observations",
        "managed_models",
        "managed_projects",
        "managed_providers",
        "replay_llm_tokens",
        "replay_state_snapshots",
        "replay_stt_events",
        "replay_tts_frames",
        "requests",
        "sessions",
        "turns",
        "api_keys",
    }
    assert expected.issubset(names), f"missing tables: {sorted(expected - names)}"


@pytest.mark.asyncio
async def test_run_migrations_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "voicegw.db"
    db = Database(_build_config(db_path))
    try:
        await db.run_migrations()
        await db.run_migrations()  # second call is a no-op
    finally:
        await db.dispose()

    head = _current_head()
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute("SELECT version_num FROM alembic_version").fetchall()
    assert rows == [(head,)]


@pytest.mark.asyncio
async def test_views_created(tmp_path: Path) -> None:
    db_path = tmp_path / "voicegw.db"
    db = Database(_build_config(db_path))
    try:
        await db.run_migrations()
    finally:
        await db.dispose()

    with sqlite3.connect(str(db_path)) as conn:
        views = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'view'"
            )
        }
    assert {"daily_costs", "project_daily_costs"}.issubset(views)
