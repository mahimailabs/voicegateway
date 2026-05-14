"""Contract tests for migration 0007 guardrails schema."""

from __future__ import annotations

from importlib import import_module

import aiosqlite

from voicegateway.services.storage_service import StorageService

_migration = import_module("voicegateway.storage.migrations.0007_guardrails")


async def test_guardrail_events_table_created(tmp_path) -> None:
    db_path = str(tmp_path / "fresh.db")
    storage = StorageService(db_path)
    await storage._ensure_initialized()

    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='guardrail_events'"
        )
        assert await cur.fetchone() is not None

        cur = await db.execute("PRAGMA table_info(guardrail_events)")
        cols = {row[1] async for row in cur}
        assert cols == {
            "id",
            "event_type",
            "session_id",
            "tenant_id",
            "turn_index",
            "category",
            "action",
            "context_excerpt",
            "created_at",
        }


async def test_sessions_and_projects_gain_guardrail_columns(tmp_path) -> None:
    db_path = str(tmp_path / "fresh.db")
    storage = StorageService(db_path)
    await storage._ensure_initialized()

    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute("PRAGMA table_info(sessions)")
        session_cols = {row[1] async for row in cur}
        cur = await db.execute("PRAGMA table_info(managed_projects)")
        project_cols = {row[1] async for row in cur}

    assert "guardrails_active" in session_cols
    assert "guardrails_bypassed" in session_cols
    assert "guardrail_policy_snapshot_json" in session_cols
    assert "guardrail_policy_json" in project_cols


async def test_migration_is_idempotent(tmp_path) -> None:
    db_path = str(tmp_path / "fresh.db")
    storage = StorageService(db_path)
    await storage._ensure_initialized()

    async with aiosqlite.connect(db_path) as db:
        await _migration.apply(db)
        await db.commit()

        cur = await db.execute("PRAGMA table_info(sessions)")
        cols = [row for row in await cur.fetchall() if row[1] == "guardrails_active"]
        assert len(cols) == 1


async def test_existing_session_rows_keep_null_guardrail_state(tmp_path) -> None:
    db_path = str(tmp_path / "with-row.db")
    storage = StorageService(db_path)
    await storage._ensure_initialized()

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO sessions (id, project, started_at, modalities) "
            "VALUES ('legacy', 'default', '2026-05-01T00:00', '')"
        )
        await db.commit()
        await _migration.apply(db)
        await db.commit()

        cur = await db.execute(
            "SELECT guardrails_active, guardrails_bypassed, "
            "guardrail_policy_snapshot_json FROM sessions WHERE id = 'legacy'"
        )
        assert await cur.fetchone() == (None, None, None)
