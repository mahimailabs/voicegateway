"""Contract tests for migration 0006 (routing columns + branding + latency_observations)."""

from __future__ import annotations

from importlib import import_module

import aiosqlite

from voicegateway.services.storage_service import StorageService

_migration = import_module("voicegateway.storage.migrations.0006_routing_and_branding")


async def test_sessions_gains_five_routing_columns(tmp_path) -> None:
    db_path = str(tmp_path / "fresh.db")
    storage = StorageService(db_path)
    await storage._ensure_initialized()

    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute("PRAGMA table_info(sessions)")
        cols = {row[1] async for row in cur}

    for col in (
        "budget_ms",
        "budget_ms_used",
        "budget_overrun",
        "routed_llm",
        "routed_tts",
    ):
        assert col in cols, f"missing sessions.{col}"


async def test_managed_projects_gains_branding_json(tmp_path) -> None:
    db_path = str(tmp_path / "fresh.db")
    storage = StorageService(db_path)
    await storage._ensure_initialized()

    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute("PRAGMA table_info(managed_projects)")
        cols = {row[1] async for row in cur}

    assert "branding_json" in cols


async def test_latency_observations_table_created(tmp_path) -> None:
    db_path = str(tmp_path / "fresh.db")
    storage = StorageService(db_path)
    await storage._ensure_initialized()

    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='latency_observations'"
        )
        assert await cur.fetchone() is not None

        cur = await db.execute("PRAGMA table_info(latency_observations)")
        cols = {row[1] async for row in cur}
        assert cols == {
            "id",
            "project_id",
            "provider",
            "modality",
            "p50_ms",
            "p95_ms",
            "sample_count",
            "window_start",
            "window_end",
            "refreshed_at",
        }


async def test_composite_index_present(tmp_path) -> None:
    db_path = str(tmp_path / "fresh.db")
    storage = StorageService(db_path)
    await storage._ensure_initialized()

    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND tbl_name='latency_observations'"
        )
        names = {row[0] async for row in cur}

    assert "idx_latency_obs_project_provider" in names


async def test_is_idempotent(tmp_path) -> None:
    db_path = str(tmp_path / "fresh.db")
    storage = StorageService(db_path)
    await storage._ensure_initialized()

    async with aiosqlite.connect(db_path) as db:
        await _migration.apply(db)
        await db.commit()

        cur = await db.execute("PRAGMA table_info(sessions)")
        budget_cols = [row for row in await cur.fetchall() if row[1] == "budget_ms"]
        assert len(budget_cols) == 1


async def test_v005_baseline_skips_missing_managed_projects(tmp_path) -> None:
    """An install without managed_projects (no projects configured) is OK."""
    db_path = str(tmp_path / "v005.db")
    async with aiosqlite.connect(db_path) as db:
        await db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, project TEXT)")
        await db.commit()
        await _migration.apply(db)
        await db.commit()

        cur = await db.execute("PRAGMA table_info(sessions)")
        cols = {row[1] async for row in cur}
        assert "budget_ms" in cols

        cur = await db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='managed_projects'"
        )
        assert await cur.fetchone() is None  # Still absent; ALTER skipped.


async def test_preexisting_sessions_get_null(tmp_path) -> None:
    """OQ3-like: pre-v0.5.0 rows keep NULL on the new columns."""
    db_path = str(tmp_path / "with_rows.db")
    storage = StorageService(db_path)
    await storage._ensure_initialized()

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO sessions (id, project, started_at, modalities, "
            "total_cost_usd, request_count) "
            "VALUES ('legacy', 'default', '2026-05-01T00:00', '', 0.0, 0)"
        )
        await db.commit()
        cur = await db.execute(
            "SELECT budget_ms, budget_overrun, routed_llm, routed_tts FROM sessions WHERE id = 'legacy'"
        )
        row = await cur.fetchone()
        assert row == (None, None, None, None)
