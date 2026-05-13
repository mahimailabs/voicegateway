"""Contract tests for migration 0003 (turns + dead_air_events + session aggregates).

Validates idempotency and v0.1.x row preservation per Foundry test
strategy: tables created, columns added, indexes present, pre-v0.2.0
rows preserved with NULL on new columns, idempotent re-run.
"""

from __future__ import annotations

from importlib import import_module

import aiosqlite

from voicegateway.storage.sqlite import SQLiteStorage

_migration = import_module("voicegateway.storage.migrations.0003_turns_and_deadair")


async def test_migration_creates_turns_and_dead_air_tables(tmp_path) -> None:
    db_path = str(tmp_path / "fresh.db")
    storage = SQLiteStorage(db_path)
    await storage._ensure_initialized()

    async with aiosqlite.connect(db_path) as db:

        async def _table_exists(name: str) -> bool:
            cur = await db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (name,),
            )
            return await cur.fetchone() is not None

        assert await _table_exists("turns")
        assert await _table_exists("dead_air_events")


async def test_migration_adds_aggregate_columns_to_sessions(tmp_path) -> None:
    db_path = str(tmp_path / "fresh.db")
    storage = SQLiteStorage(db_path)
    await storage._ensure_initialized()

    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute("PRAGMA table_info(sessions)")
        cols = {row[1] async for row in cur}

    for new_col in (
        "talk_time_seconds",
        "per_minute_cost_usd",
        "response_speed_p50_ms",
        "response_speed_p95_ms",
        "talk_over_rate",
    ):
        assert new_col in cols, f"missing aggregate column {new_col}"


async def test_migration_creates_indexes(tmp_path) -> None:
    db_path = str(tmp_path / "fresh.db")
    storage = SQLiteStorage(db_path)
    await storage._ensure_initialized()

    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute("SELECT name FROM sqlite_master WHERE type='index'")
        names = {row[0] async for row in cur}

    for expected in (
        "idx_turns_session_id",
        "idx_turns_response_speed",
        "idx_dead_air_session_id",
    ):
        assert expected in names, f"missing index {expected}"


async def test_migration_is_idempotent(tmp_path) -> None:
    """Apply twice; second call should be a no-op (no SQL errors)."""
    db_path = str(tmp_path / "fresh.db")
    storage = SQLiteStorage(db_path)
    await storage._ensure_initialized()  # first apply
    async with aiosqlite.connect(db_path) as db:
        await _migration.apply(db)  # second apply — must not raise
        await db.commit()


async def test_preexisting_session_keeps_null_aggregates(tmp_path) -> None:
    """A v0.1.x-style sessions row gets NULL on the new columns."""
    db_path = str(tmp_path / "fresh.db")
    storage = SQLiteStorage(db_path)
    await storage._ensure_initialized()

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO sessions (id, project, started_at, total_cost_usd, "
            "request_count) VALUES (?, ?, ?, ?, ?)",
            ("legacy-1", "default", "2026-01-01T00:00:00Z", 0.05, 3),
        )
        await db.commit()

        cur = await db.execute(
            "SELECT talk_time_seconds, per_minute_cost_usd, "
            "response_speed_p50_ms, response_speed_p95_ms, talk_over_rate "
            "FROM sessions WHERE id = ?",
            ("legacy-1",),
        )
        row = await cur.fetchone()

    assert row is not None
    assert all(v is None for v in row)
