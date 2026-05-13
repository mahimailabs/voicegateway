"""Contract tests for migration 0005 (tenant_id columns + virtual_keys table).

Covers REQ-VG-TENANT-001 (tenant_id on every cost / metric / replay
row) and REQ-VG-TENANT-003 (virtual_keys table). Two scenarios:

- v0.0.5-only baseline: migrations 0003 and 0004 have not run, so the
  ``turns`` / ``dead_air_events`` / ``replay_*`` tables don't exist
  yet. Migration 0005 must skip the conditional ALTERs silently (OQ4
  lock).
- Fully-migrated baseline: every prior migration has run, so the
  conditional ALTERs land tenant_id on the derived tables.
"""

from __future__ import annotations

from importlib import import_module

import aiosqlite

from voicegateway.storage.sqlite import SQLiteStorage

_migration = import_module("voicegateway.storage.migrations.0005_tenant_attribution")


async def test_creates_virtual_keys_table(tmp_path) -> None:
    db_path = str(tmp_path / "fresh.db")
    storage = SQLiteStorage(db_path)
    await storage._ensure_initialized()

    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='virtual_keys'"
        )
        assert await cur.fetchone() is not None


async def test_virtual_keys_columns_match_design(tmp_path) -> None:
    db_path = str(tmp_path / "fresh.db")
    storage = SQLiteStorage(db_path)
    await storage._ensure_initialized()

    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute("PRAGMA table_info(virtual_keys)")
        cols = {row[1] async for row in cur}

    assert cols == {
        "id",
        "key_prefix",
        "key_hash",
        "name",
        "tenant_id",
        "issued_by",
        "issued_at",
        "last_used_at",
        "revoked_at",
    }


async def test_virtual_keys_indexes_present(tmp_path) -> None:
    db_path = str(tmp_path / "fresh.db")
    storage = SQLiteStorage(db_path)
    await storage._ensure_initialized()

    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND tbl_name='virtual_keys'"
        )
        names = {row[0] async for row in cur}

    assert "idx_virtual_keys_prefix" in names
    assert "idx_virtual_keys_tenant_id" in names


async def test_adds_tenant_id_to_baseline_tables(tmp_path) -> None:
    """sessions and requests get tenant_id unconditionally (v0.0.5 baseline)."""
    db_path = str(tmp_path / "fresh.db")
    storage = SQLiteStorage(db_path)
    await storage._ensure_initialized()

    async with aiosqlite.connect(db_path) as db:
        for table in ("sessions", "requests"):
            cur = await db.execute(f"PRAGMA table_info({table})")
            cols = {row[1] async for row in cur}
            assert "tenant_id" in cols, f"{table}.tenant_id missing"


async def test_adds_tenant_id_to_derived_tables(tmp_path) -> None:
    """turns, dead_air_events, and replay_* get tenant_id when present."""
    db_path = str(tmp_path / "fresh.db")
    storage = SQLiteStorage(db_path)
    await storage._ensure_initialized()

    async with aiosqlite.connect(db_path) as db:
        for table in (
            "turns",
            "dead_air_events",
            "replay_stt_events",
            "replay_llm_tokens",
            "replay_tts_frames",
            "replay_state_snapshots",
        ):
            cur = await db.execute(f"PRAGMA table_info({table})")
            cols = {row[1] async for row in cur}
            assert "tenant_id" in cols, f"{table}.tenant_id missing"


async def test_is_idempotent(tmp_path) -> None:
    """Re-running the migration on an already-migrated DB is a no-op."""
    db_path = str(tmp_path / "fresh.db")
    storage = SQLiteStorage(db_path)
    await storage._ensure_initialized()

    async with aiosqlite.connect(db_path) as db:
        # Re-apply manually; should not raise on duplicate column adds.
        await _migration.apply(db)
        await db.commit()

        # Verify the column is still exactly one column, not duplicated.
        cur = await db.execute("PRAGMA table_info(sessions)")
        tenant_cols = [row for row in await cur.fetchall() if row[1] == "tenant_id"]
        assert len(tenant_cols) == 1


async def test_v005_baseline_skips_missing_tables(tmp_path) -> None:
    """OQ4: ALTERs on absent v0.2.0/v0.3.0 tables don't error."""
    db_path = str(tmp_path / "v005.db")

    # Build a v0.0.5-only baseline by hand: sessions + requests, no
    # turns / dead_air / replay_* tables. Then run migration 0005
    # directly and verify it doesn't raise.
    async with aiosqlite.connect(db_path) as db:
        await db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, project TEXT)")
        await db.execute("CREATE TABLE requests (id TEXT PRIMARY KEY, project TEXT)")
        await db.commit()

        await _migration.apply(db)
        await db.commit()

        cur = await db.execute("PRAGMA table_info(sessions)")
        cols = {row[1] async for row in cur}
        assert "tenant_id" in cols

        cur = await db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='turns'"
        )
        assert await cur.fetchone() is None  # Still absent; ALTER was skipped.


async def test_preexisting_rows_get_null_tenant_id(tmp_path) -> None:
    """OQ3: no backfill; pre-v0.4.0 sessions stay NULL."""
    db_path = str(tmp_path / "with_rows.db")
    storage = SQLiteStorage(db_path)
    await storage._ensure_initialized()

    # Insert a row via the public API path (RequestRecord -> log_request
    # would also stamp tenant from ContextVar; this insert simulates a
    # pre-v0.4.0 row that pre-dates the ContextVar layer).
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO sessions (id, project, started_at, ended_at, "
            "modalities, total_cost_usd, request_count) "
            "VALUES ('legacy', 'default', '2026-01-01T00:00', NULL, '', 0.0, 0)"
        )
        await db.commit()
        cur = await db.execute("SELECT tenant_id FROM sessions WHERE id = 'legacy'")
        row = await cur.fetchone()
        assert row is not None
        assert row[0] is None
