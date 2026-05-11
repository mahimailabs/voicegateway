"""Contract tests for migration 0004 (replay tables + sessions.replay_size_bytes)."""

from __future__ import annotations

from importlib import import_module

import aiosqlite

from voicegateway.storage.sqlite import SQLiteStorage

_migration = import_module("voicegateway.storage.migrations.0004_replay_tables")


async def test_creates_all_four_replay_tables(tmp_path) -> None:
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

        for table in (
            "replay_stt_events",
            "replay_llm_tokens",
            "replay_tts_frames",
            "replay_state_snapshots",
        ):
            assert await _table_exists(table), f"missing table {table}"


async def test_adds_replay_size_bytes_to_sessions(tmp_path) -> None:
    db_path = str(tmp_path / "fresh.db")
    storage = SQLiteStorage(db_path)
    await storage._ensure_initialized()

    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute("PRAGMA table_info(sessions)")
        cols = {row[1] async for row in cur}

    assert "replay_size_bytes" in cols


async def test_creates_composite_indexes(tmp_path) -> None:
    db_path = str(tmp_path / "fresh.db")
    storage = SQLiteStorage(db_path)
    await storage._ensure_initialized()

    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute("SELECT name FROM sqlite_master WHERE type='index'")
        names = {row[0] async for row in cur}

    for expected in (
        "idx_replay_stt_session_t",
        "idx_replay_llm_session_t",
        "idx_replay_tts_session_t",
        "idx_replay_state_session_t",
    ):
        assert expected in names, f"missing index {expected}"


async def test_idempotent_reapply(tmp_path) -> None:
    db_path = str(tmp_path / "fresh.db")
    storage = SQLiteStorage(db_path)
    await storage._ensure_initialized()
    async with aiosqlite.connect(db_path) as db:
        await _migration.apply(db)  # second apply — must not raise
        await db.commit()


async def test_preexisting_session_keeps_null_replay_size(tmp_path) -> None:
    """A v0.2.0-era sessions row is left with NULL on replay_size_bytes."""
    db_path = str(tmp_path / "fresh.db")
    storage = SQLiteStorage(db_path)
    await storage._ensure_initialized()

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO sessions "
            "(id, project, started_at, total_cost_usd, request_count) "
            "VALUES (?, ?, ?, ?, ?)",
            ("legacy-pre-v030", "default", "2026-04-01T00:00:00Z", 0.0, 0),
        )
        await db.commit()

        cur = await db.execute(
            "SELECT replay_size_bytes FROM sessions WHERE id = ?",
            ("legacy-pre-v030",),
        )
        row = await cur.fetchone()

    assert row is not None
    assert row[0] is None
