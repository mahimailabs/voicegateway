"""Alembic migration e4a7c2f9b1d3: calls + call_legs + sessions correlation columns.

Runs ``alembic upgrade head`` against a throwaway SQLite file (never a configured
database) and checks the DDL the migration actually produces, including that it
has not drifted from the SQLModel definitions -- nothing in this repo runs
autogenerate, so the migration and the model can disagree silently.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, text
from sqlmodel import SQLModel

from voicegateway.core.config import GatewayConfig
from voicegateway.core.database import Database
from voicegateway.models.call_leg_model import CallLeg  # noqa: F401 - registers table
from voicegateway.models.call_model import Call  # noqa: F401 - registers table


async def _migrated_engine(tmp_path: Path) -> tuple[Database, sa.Engine]:
    db = Database(GatewayConfig(cost_tracking={"db_path": str(tmp_path / "calls.db")}))
    await db.run_migrations()
    return db, create_engine(f"sqlite:///{db.db_file_path}")


def _columns(engine: sa.Engine, table: str) -> dict[str, tuple[str, int]]:
    """``{column: (declared_type, notnull)}`` as SQLite reports it."""
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).all()
    return {row[1]: (str(row[2]).upper(), int(row[3])) for row in rows}


async def test_migration_creates_calls_and_call_legs(tmp_path: Path) -> None:
    db, engine = await _migrated_engine(tmp_path)
    try:
        with engine.connect() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type = 'table'")
                )
            }
        assert {"calls", "call_legs"}.issubset(tables)

        calls = _columns(engine, "calls")
        # room_sid / room_name nullable: a 503 on INVITE never creates a room.
        assert calls["room_sid"] == ("VARCHAR", 0)
        assert calls["room_name"] == ("VARCHAR", 0)
        # Epoch milliseconds must be BIGINT (INT4 overflows on PostgreSQL).
        for column in ("started_at_ms", "ended_at_ms", "duration_ms"):
            assert calls[column][0] == "BIGINT", f"calls.{column} is {calls[column][0]}"
        assert calls["answer_latency_ms"][0] == "BIGINT"
        assert "answer_latency_source" in calls

        legs = _columns(engine, "call_legs")
        assert legs["participant_sid"][1] == 1, "participant_sid must be NOT NULL"
        for column in ("joined_at_ms", "left_at_ms", "first_audio_track_at_ms"):
            assert legs[column][0] == "BIGINT"
    finally:
        engine.dispose()
        await db.dispose()


async def test_migration_adds_sessions_correlation_columns(tmp_path: Path) -> None:
    db, engine = await _migrated_engine(tmp_path)
    try:
        sessions = _columns(engine, "sessions")
        assert "room_name" in sessions
        assert "call_id" in sessions
        assert sessions["room_name"][1] == 0, "must be nullable (no backfill exists)"
        assert sessions["call_id"][1] == 0

        with engine.connect() as conn:
            indexes = {
                row[0]
                for row in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type = 'index'")
                )
            }
        assert {"idx_sessions_room_name", "idx_sessions_call_id"}.issubset(indexes)
        assert {
            "idx_calls_room_name",
            "idx_calls_started_at_ms",
            "idx_calls_project",
            "idx_calls_run_id",
        }.issubset(indexes)
    finally:
        engine.dispose()
        await db.dispose()


async def test_room_sid_uniqueness_is_enforced_but_nulls_are_not(
    tmp_path: Path,
) -> None:
    """The unique constraint reached the DDL -- and two keyless INVITEs still fit,
    which is exactly why the repository upserts select-then-update."""
    db, engine = await _migrated_engine(tmp_path)
    insert = text(
        "INSERT INTO calls (id, room_sid, origin, project) "
        "VALUES (:id, :room_sid, 'webhook', 'default')"
    )
    try:
        with engine.begin() as conn:
            conn.execute(insert, {"id": "c1", "room_sid": "RM_1"})
            conn.execute(insert, {"id": "c2", "room_sid": None})
            conn.execute(insert, {"id": "c3", "room_sid": None})
        with pytest.raises(sa.exc.IntegrityError):
            with engine.begin() as conn:
                conn.execute(insert, {"id": "c4", "room_sid": "RM_1"})
    finally:
        engine.dispose()
        await db.dispose()


@pytest.mark.parametrize("table", ["calls", "call_legs"])
async def test_migration_matches_the_model(tmp_path: Path, table: str) -> None:
    """The handwritten migration must produce what ``create_all`` would.

    Nothing here runs alembic autogenerate, so a column added to the model and
    forgotten in the migration (or vice versa) is invisible until a fresh install
    diverges from an upgraded one.
    """
    db, migrated = await _migrated_engine(tmp_path)
    declared = create_engine(f"sqlite:///{tmp_path / 'declared.db'}")
    try:
        SQLModel.metadata.create_all(
            declared, tables=[SQLModel.metadata.tables[table]]
        )
        assert _columns(migrated, table) == _columns(declared, table)
    finally:
        declared.dispose()
        migrated.dispose()
        await db.dispose()
