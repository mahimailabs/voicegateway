"""Contract tests for voicegateway.storage.retention_worker (T06 of v0.3.0)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import aiosqlite
import pytest

from voicegateway.middleware.replay_capture import ReplayEvent
from voicegateway.storage import replay_repo
from voicegateway.storage.retention_worker import RetentionWorker
from voicegateway.storage.sqlite import SQLiteStorage


async def _seed_session(
    db_path: str,
    session_id: str,
    project: str,
    ended_at_offset_days: float,
) -> None:
    """Insert a sessions row with a project + an `ended_at` in the past."""
    ended_at = (
        datetime.now(UTC) - timedelta(days=ended_at_offset_days)
    ).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO sessions "
            "(id, project, started_at, ended_at, total_cost_usd, request_count) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, project, ended_at, ended_at, 0.0, 0),
        )
        await replay_repo.bulk_write_events(
            db,
            [
                ReplayEvent(
                    session_id=session_id,
                    modality="stt",
                    t_ms=0,
                    payload={"text": "x", "is_final": True, "alternatives": []},
                    provider="deepgram",
                    cost_usd=0.0,
                )
            ],
        )


@pytest.fixture
async def storage(tmp_path):
    db_path = str(tmp_path / "retention.db")
    s = SQLiteStorage(db_path)
    await s._ensure_initialized()
    return s


async def test_deletes_rows_older_than_window(storage, tmp_path) -> None:
    db_path = str(tmp_path / "retention.db")
    await _seed_session(db_path, "old-1", "acme", ended_at_offset_days=10)
    await _seed_session(db_path, "young-1", "acme", ended_at_offset_days=1)

    async def provider():
        return [("acme", 5)]  # 5-day retention window

    worker = RetentionWorker(storage, retention_provider=provider)
    deletes = await worker.tick_now()
    assert deletes["acme"] >= 1

    # Verify old session's replay rows are gone, young's are kept.
    async with aiosqlite.connect(db_path) as db:
        old_left = await replay_repo.read_full_replay(db, "old-1")
        young_left = await replay_repo.read_full_replay(db, "young-1")
    assert old_left == []
    assert len(young_left) == 1


async def test_no_projects_no_deletes(storage) -> None:
    async def provider():
        return []

    worker = RetentionWorker(storage, retention_provider=provider)
    deletes = await worker.tick_now()
    assert deletes == {}


async def test_in_flight_sessions_never_deleted(storage, tmp_path) -> None:
    """Sessions with ended_at IS NULL must not be touched."""
    db_path = str(tmp_path / "retention.db")
    async with aiosqlite.connect(db_path) as db:
        # In-flight session: ended_at is NULL.
        await db.execute(
            "INSERT INTO sessions "
            "(id, project, started_at, ended_at, total_cost_usd, request_count) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "in-flight-1",
                "acme",
                "2026-04-01T00:00:00Z",
                None,
                0.0,
                0,
            ),
        )
        await replay_repo.bulk_write_events(
            db,
            [
                ReplayEvent(
                    session_id="in-flight-1",
                    modality="stt",
                    t_ms=0,
                    payload={"text": "live"},
                    provider="d",
                    cost_usd=0.0,
                )
            ],
        )

    async def provider():
        return [("acme", 1)]  # 1-day retention — would delete anything closed yesterday

    worker = RetentionWorker(storage, retention_provider=provider)
    await worker.tick_now()

    async with aiosqlite.connect(db_path) as db:
        kept = await replay_repo.read_full_replay(db, "in-flight-1")
    assert len(kept) == 1


async def test_idempotent_across_ticks(storage, tmp_path) -> None:
    db_path = str(tmp_path / "retention.db")
    await _seed_session(db_path, "old-1", "acme", ended_at_offset_days=10)

    async def provider():
        return [("acme", 5)]

    worker = RetentionWorker(storage, retention_provider=provider)
    first = await worker.tick_now()
    second = await worker.tick_now()
    assert first["acme"] >= 1
    assert second["acme"] == 0  # already deleted


async def test_invalid_retention_days_falls_back_to_default(storage, tmp_path) -> None:
    db_path = str(tmp_path / "retention.db")
    await _seed_session(db_path, "old-1", "acme", ended_at_offset_days=100)

    async def provider():
        return [("acme", 0)]  # invalid: < 1

    worker = RetentionWorker(
        storage,
        retention_provider=provider,
        default_retention_days=90,
    )
    deletes = await worker.tick_now()
    # 100 days old > 90 default → should still be deleted.
    assert deletes["acme"] >= 1
