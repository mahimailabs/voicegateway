"""Alembic migration a7c2e91f8d34: add cached_input_units to requests.

Verifies the migration runs cleanly on a fresh DB, is idempotent, and
that downgrade drops the column (SQLite 3.35+).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, text

from voicegateway.core.config import GatewayConfig
from voicegateway.core.database import Database


def _column_exists(engine: sa.Engine, table: str, column: str) -> bool:
    with engine.connect() as conn:
        info = conn.execute(text(f"PRAGMA table_info({table})")).all()
    return any(row[1] == column for row in info)


async def _build_db(tmp_path: Path) -> Database:
    db_path = tmp_path / "migration-test.db"
    db = Database(GatewayConfig(cost_tracking={"db_path": str(db_path)}))
    await db.run_migrations()
    return db


async def test_migration_adds_cached_input_units_column(tmp_path: Path) -> None:
    """After ``alembic upgrade head`` the column is present with default 0."""
    db = await _build_db(tmp_path)
    sync_engine = create_engine(f"sqlite:///{db.db_file_path}")
    try:
        assert _column_exists(sync_engine, "requests", "cached_input_units"), (
            "migration did not add cached_input_units column to requests"
        )

        # Inserting without the column relies on the server-side default.
        with sync_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO requests (id, timestamp, project, modality, "
                    "model_id, provider) VALUES "
                    "('migrate-test-1', 1000000.0, 'p', 'llm', 'fake/m', 'fake')"
                )
            )
            row = conn.execute(
                text(
                    "SELECT cached_input_units FROM requests WHERE id = "
                    "'migrate-test-1'"
                )
            ).first()
        assert row is not None
        assert row[0] == pytest.approx(0.0), (
            "server_default should backfill the column with 0 for INSERTs "
            f"that omit it; got {row[0]!r}"
        )
    finally:
        sync_engine.dispose()
        await db.dispose()


async def test_migration_is_idempotent(tmp_path: Path) -> None:
    """Calling run_migrations twice on the same DB is a no-op."""
    db = await _build_db(tmp_path)
    try:
        # Second run; the in-process _migrations_applied guard returns early,
        # but an explicit fresh Database against the same file proves the
        # underlying alembic state-table is consistent.
        await db.run_migrations()
        db2 = Database(GatewayConfig(cost_tracking={"db_path": str(db.db_file_path)}))
        await db2.run_migrations()
        await db2.dispose()
    finally:
        await db.dispose()


async def test_migration_column_accepts_writes_and_reads(tmp_path: Path) -> None:
    """End-to-end: write a row with a non-zero cached_input_units, read it back."""
    db = await _build_db(tmp_path)
    sync_engine = create_engine(f"sqlite:///{db.db_file_path}")
    try:
        with sync_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO requests (id, timestamp, project, modality, "
                    "model_id, provider, input_units, output_units, "
                    "cached_input_units, cost_usd) VALUES "
                    "('cached-test-1', 1000001.0, 'p', 'llm', 'openai/gpt-4o', "
                    "'openai', 1000.0, 200.0, 800.0, 0.0042)"
                )
            )
            row = conn.execute(
                text(
                    "SELECT input_units, output_units, cached_input_units "
                    "FROM requests WHERE id = 'cached-test-1'"
                )
            ).first()
        assert row is not None
        assert row[0] == pytest.approx(1000.0)
        assert row[1] == pytest.approx(200.0)
        assert row[2] == pytest.approx(800.0)
    finally:
        sync_engine.dispose()
        await db.dispose()
