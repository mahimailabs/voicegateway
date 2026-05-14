"""Auto-stamp legacy databases at the correct alembic revision.

For Commit 2, the only revision that exists in the alembic version
graph is ``0001_baseline``. Any DB-shape detected as "more recent" will
be clamped to head (0001_baseline) so ``alembic upgrade head`` is a
no-op rather than a crash. Once Commit 3 lands the other revisions
those branches start being exercised end-to-end; the detector body
already returns the right strings, only the clamping changes behavior.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from voicegateway.core.config import GatewayConfig
from voicegateway.core.database import Database, _detect_schema_level


def _build_config(db_path: Path) -> GatewayConfig:
    return GatewayConfig(cost_tracking={"db_path": str(db_path)})


def _seed_legacy_db(db_path: Path, ddl: str) -> None:
    """Write a hand-crafted legacy schema to ``db_path``."""
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(ddl)


def test_detect_fresh_db_returns_baseline(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.db"
    # No tables at all; the detector treats no-requests as "fresh".
    sqlite3.connect(str(db_path)).close()
    # Empty DB falls into the 'requests table missing' branch (handled by
    # _stamp_legacy_db_if_needed). _detect_schema_level itself returns
    # the floor of 0001_baseline.
    assert _detect_schema_level(db_path) == "0001_baseline"


def test_detect_requests_only_returns_baseline(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    _seed_legacy_db(
        db_path,
        """
        CREATE TABLE requests (id TEXT PRIMARY KEY, timestamp REAL);
        """,
    )
    assert _detect_schema_level(db_path) == "0001_baseline"


def test_detect_turns_returns_0002(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    _seed_legacy_db(
        db_path,
        """
        CREATE TABLE requests (id TEXT PRIMARY KEY, timestamp REAL);
        CREATE TABLE turns (id INTEGER PRIMARY KEY);
        """,
    )
    assert _detect_schema_level(db_path) == "0002_turns_and_deadair"


def test_detect_replay_returns_0003(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    _seed_legacy_db(
        db_path,
        """
        CREATE TABLE requests (id TEXT PRIMARY KEY, timestamp REAL);
        CREATE TABLE turns (id INTEGER PRIMARY KEY);
        CREATE TABLE replay_stt_events (id INTEGER PRIMARY KEY);
        """,
    )
    assert _detect_schema_level(db_path) == "0003_replay_tables"


def test_detect_tenant_id_returns_0004(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    _seed_legacy_db(
        db_path,
        """
        CREATE TABLE requests (id TEXT PRIMARY KEY, timestamp REAL, tenant_id TEXT);
        CREATE TABLE turns (id INTEGER PRIMARY KEY);
        CREATE TABLE replay_stt_events (id INTEGER PRIMARY KEY);
        """,
    )
    assert _detect_schema_level(db_path) == "0004_tenant_attribution"


def test_detect_routing_returns_0005(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    _seed_legacy_db(
        db_path,
        """
        CREATE TABLE requests (id TEXT PRIMARY KEY, timestamp REAL, tenant_id TEXT);
        CREATE TABLE sessions (id TEXT PRIMARY KEY, routed_llm TEXT);
        CREATE TABLE turns (id INTEGER PRIMARY KEY);
        CREATE TABLE replay_stt_events (id INTEGER PRIMARY KEY);
        """,
    )
    assert _detect_schema_level(db_path) == "0005_routing_and_branding"


def test_detect_guardrails_returns_0006(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    _seed_legacy_db(
        db_path,
        """
        CREATE TABLE requests (id TEXT PRIMARY KEY, timestamp REAL);
        CREATE TABLE guardrail_events (id INTEGER PRIMARY KEY);
        """,
    )
    assert _detect_schema_level(db_path) == "0006_guardrails"


def test_detect_branding_returns_0005(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    _seed_legacy_db(
        db_path,
        """
        CREATE TABLE requests (id TEXT PRIMARY KEY, timestamp REAL);
        CREATE TABLE managed_projects (
            project_id TEXT PRIMARY KEY, branding_json TEXT
        );
        """,
    )
    assert _detect_schema_level(db_path) == "0005_routing_and_branding"


def test_detect_guardrails_via_sessions_column(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    _seed_legacy_db(
        db_path,
        """
        CREATE TABLE requests (id TEXT PRIMARY KEY, timestamp REAL);
        CREATE TABLE sessions (id TEXT PRIMARY KEY, guardrails_active INTEGER);
        """,
    )
    assert _detect_schema_level(db_path) == "0006_guardrails"


@pytest.mark.asyncio
async def test_run_migrations_legacy_baseline_db_upgrades_to_head(
    tmp_path: Path,
) -> None:
    """A legacy DB at the full v0.0.5 baseline shape stamps then upgrades.

    Seeds the same tables ``storage/schema.py:SCHEMA_SQL`` produced
    before the alembic cutover, then drives ``Database.run_migrations``.
    Stamping detects baseline; alembic upgrade runs 0002 through 0006
    forward, each PRAGMA-guarded so they happily ADD COLUMN on the
    legacy table.
    """
    db_path = tmp_path / "legacy.db"
    _seed_legacy_db(
        db_path,
        """
        CREATE TABLE requests (
            id TEXT PRIMARY KEY,
            timestamp REAL NOT NULL,
            project TEXT NOT NULL DEFAULT 'default',
            modality TEXT NOT NULL,
            model_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            input_units REAL DEFAULT 0,
            output_units REAL DEFAULT 0,
            cost_usd REAL DEFAULT 0,
            pricing_source TEXT NOT NULL DEFAULT '',
            ttfb_ms REAL,
            total_latency_ms REAL,
            status TEXT DEFAULT 'success',
            fallback_from TEXT,
            error_message TEXT,
            metadata TEXT,
            session_id TEXT
        );
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            project TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            modalities TEXT NOT NULL DEFAULT '',
            total_cost_usd REAL DEFAULT 0,
            request_count INTEGER DEFAULT 0
        );
        CREATE TABLE managed_providers (
            provider_id TEXT PRIMARY KEY,
            provider_type TEXT NOT NULL,
            api_key_encrypted TEXT NOT NULL DEFAULT '',
            base_url TEXT,
            extra_config TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            project TEXT
        );
        CREATE TABLE managed_models (
            model_id TEXT PRIMARY KEY,
            modality TEXT NOT NULL,
            provider_id TEXT NOT NULL,
            model_name TEXT NOT NULL,
            display_name TEXT,
            default_language TEXT,
            default_voice TEXT,
            extra_config TEXT NOT NULL DEFAULT '{}',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE managed_projects (
            project_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            daily_budget REAL NOT NULL DEFAULT 0,
            budget_action TEXT NOT NULL DEFAULT 'warn',
            default_stack TEXT,
            stt_model TEXT,
            llm_model TEXT,
            tts_model TEXT,
            tags TEXT NOT NULL DEFAULT '[]',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE config_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            action TEXT NOT NULL,
            changes_json TEXT,
            source TEXT NOT NULL DEFAULT 'api'
        );
        """,
    )
    db = Database(_build_config(db_path))
    try:
        await db.run_migrations()
    finally:
        await db.dispose()

    with sqlite3.connect(str(db_path)) as conn:
        ver = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        sessions_cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert ver == ("0006_guardrails",)
    # Migrations 0002 + 0006 added these column families to sessions.
    assert "talk_time_seconds" in sessions_cols
    assert "routed_llm" in sessions_cols
    assert "guardrails_active" in sessions_cols
    # Migrations 0003 + 0006 added these new tables to the legacy DB.
    assert {"replay_stt_events", "guardrail_events"}.issubset(tables)
