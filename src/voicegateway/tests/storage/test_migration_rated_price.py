"""Alembic migration c7d2a9f1e6b4: add rated_price_usd + rate_rule to requests.

Verifies the two billing columns land on a fresh migrated DB with the right
server defaults, and that a row written through the raw insert path round-trips.
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
    db_path = tmp_path / "rated-price-migration.db"
    db = Database(GatewayConfig(cost_tracking={"db_path": str(db_path)}))
    await db.run_migrations()
    return db


async def test_migration_adds_billing_columns_with_defaults(tmp_path: Path) -> None:
    db = await _build_db(tmp_path)
    sync_engine = create_engine(f"sqlite:///{db.db_file_path}")
    try:
        assert _column_exists(sync_engine, "requests", "rated_price_usd")
        assert _column_exists(sync_engine, "requests", "rate_rule")

        # An INSERT that omits both columns must backfill from server defaults.
        with sync_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO requests (id, timestamp, project, modality, "
                    "model_id, provider) VALUES "
                    "('rated-1', 1000000.0, 'p', 'stt', 'deepgram/nova-3', "
                    "'deepgram')"
                )
            )
            row = conn.execute(
                text(
                    "SELECT rated_price_usd, rate_rule FROM requests "
                    "WHERE id = 'rated-1'"
                )
            ).first()
        assert row is not None
        assert row[0] == pytest.approx(0.0)
        assert row[1] == ""
    finally:
        sync_engine.dispose()
        await db.dispose()


async def test_billing_columns_accept_writes_and_reads(tmp_path: Path) -> None:
    db = await _build_db(tmp_path)
    sync_engine = create_engine(f"sqlite:///{db.db_file_path}")
    try:
        with sync_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO requests (id, timestamp, project, modality, "
                    "model_id, provider, cost_usd, rated_price_usd, rate_rule) "
                    "VALUES ('rated-2', 1000001.0, 'p', 'stt', 'deepgram/nova-3', "
                    "'deepgram', 0.0048, 0.0072, 'cost_plus:1.5')"
                )
            )
            row = conn.execute(
                text(
                    "SELECT cost_usd, rated_price_usd, rate_rule FROM requests "
                    "WHERE id = 'rated-2'"
                )
            ).first()
        assert row is not None
        assert row[0] == pytest.approx(0.0048)
        assert row[1] == pytest.approx(0.0072)
        assert row[2] == "cost_plus:1.5"
    finally:
        sync_engine.dispose()
        await db.dispose()
