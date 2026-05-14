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
async def test_run_migrations_legacy_stamps_then_upgrades(tmp_path: Path) -> None:
    """A legacy DB at the 0004 schema level stamps then upgrades to head.

    The detector returns ``0004_tenant_attribution``; alembic stamps
    that, then ``upgrade head`` runs 0005 + 0006 (each is a PRAGMA-
    guarded no-op against the legacy DB's missing columns, which the
    ALTER TABLE branch happily adds).
    """
    db_path = tmp_path / "legacy.db"
    _seed_legacy_db(
        db_path,
        """
        CREATE TABLE requests (id TEXT PRIMARY KEY, timestamp REAL, tenant_id TEXT);
        CREATE TABLE sessions (id TEXT PRIMARY KEY, tenant_id TEXT);
        """,
    )
    db = Database(_build_config(db_path))
    try:
        await db.run_migrations()
    finally:
        await db.dispose()

    with sqlite3.connect(str(db_path)) as conn:
        ver = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        # head reached after 0005 + 0006 forward-applied.
        sessions_cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    assert ver == ("0006_guardrails",)
    # Routing + guardrail columns appeared via the forward upgrades.
    assert "routed_llm" in sessions_cols
    assert "guardrails_active" in sessions_cols
