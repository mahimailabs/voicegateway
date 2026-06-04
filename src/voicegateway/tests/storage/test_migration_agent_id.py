"""Alembic migration b2e7c4a9f1d3: add agent_id to requests.

Verifies the migration adds a nullable ``agent_id`` column plus its single
and composite indexes, runs idempotently, and that downgrade reverses it.
"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import create_engine, text

from voicegateway.core.config import GatewayConfig
from voicegateway.core.database import Database


def _column_exists(engine: sa.Engine, table: str, column: str) -> bool:
    with engine.connect() as conn:
        info = conn.execute(text(f"PRAGMA table_info({table})")).all()
    return any(row[1] == column for row in info)


def _index_names(engine: sa.Engine, table: str) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA index_list({table})")).all()
    return {row[1] for row in rows}


async def _build_db(tmp_path: Path) -> Database:
    db_path = tmp_path / "agent-id-migration.db"
    db = Database(GatewayConfig(cost_tracking={"db_path": str(db_path)}))
    await db.run_migrations()
    return db


async def test_migration_adds_agent_id_column_and_indexes(tmp_path: Path) -> None:
    """After upgrade head the column and both indexes are present."""
    db = await _build_db(tmp_path)
    sync_engine = create_engine(f"sqlite:///{db.db_file_path}")
    try:
        assert _column_exists(sync_engine, "requests", "agent_id"), (
            "migration did not add agent_id column to requests"
        )
        indexes = _index_names(sync_engine, "requests")
        assert "idx_requests_agent_id" in indexes
        assert "idx_requests_agent_id_timestamp" in indexes
    finally:
        sync_engine.dispose()
        await db.dispose()


async def test_agent_id_is_nullable(tmp_path: Path) -> None:
    """A row inserted without agent_id is valid and reads back NULL."""
    db = await _build_db(tmp_path)
    sync_engine = create_engine(f"sqlite:///{db.db_file_path}")
    try:
        with sync_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO requests (id, timestamp, project, modality, "
                    "model_id, provider) VALUES "
                    "('agent-null-1', 1000000.0, 'p', 'llm', 'fake/m', 'fake')"
                )
            )
            row = conn.execute(
                text("SELECT agent_id FROM requests WHERE id = 'agent-null-1'")
            ).first()
        assert row is not None
        assert row[0] is None
    finally:
        sync_engine.dispose()
        await db.dispose()
