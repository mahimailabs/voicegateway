"""Contract tests for voicegateway.services.retention_service (T06 of v0.3.0)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from voicegateway.middleware.replay_capture import ReplayEvent
from voicegateway.repository import replay_repository as replay
from voicegateway.services.retention_service import RetentionWorker
from voicegateway.storage.sqlite import SQLiteStorage

_INSERT_SESSION = text(
    "INSERT INTO sessions "
    "(id, project, started_at, ended_at, total_cost_usd, request_count) "
    "VALUES (:id, :project, :started_at, :ended_at, :cost, :rc)"
)


async def _seed_session(
    storage: SQLiteStorage,
    session_id: str,
    project: str,
    ended_at_offset_days: float,
) -> None:
    """Insert a sessions row with a project + an ended_at in the past."""
    ended_at = (datetime.now(UTC) - timedelta(days=ended_at_offset_days)).isoformat()
    async with storage._conn.session() as db:
        await db.execute(
            _INSERT_SESSION,
            {
                "id": session_id,
                "project": project,
                "started_at": ended_at,
                "ended_at": ended_at,
                "cost": 0.0,
                "rc": 0,
            },
        )
        await replay.bulk_write_events(
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
    s = SQLiteStorage(str(tmp_path / "retention.db"))
    await s._ensure_initialized()
    return s


async def test_deletes_rows_older_than_window(storage) -> None:
    await _seed_session(storage, "old-1", "acme", ended_at_offset_days=10)
    await _seed_session(storage, "young-1", "acme", ended_at_offset_days=1)

    async def provider():
        return [("acme", 5)]

    worker = RetentionWorker(storage, retention_provider=provider)
    deletes = await worker.tick_now()
    assert deletes["acme"] >= 1

    async with storage._conn.session() as db:
        old_left = await replay.read_full_replay(db, "old-1")
        young_left = await replay.read_full_replay(db, "young-1")
    assert old_left == []
    assert len(young_left) == 1


async def test_no_projects_no_deletes(storage) -> None:
    async def provider():
        return []

    worker = RetentionWorker(storage, retention_provider=provider)
    deletes = await worker.tick_now()
    assert deletes == {}


async def test_in_flight_sessions_never_deleted(storage) -> None:
    """Sessions with ended_at IS NULL must not be touched."""
    async with storage._conn.session() as db:
        await db.execute(
            _INSERT_SESSION,
            {
                "id": "in-flight-1",
                "project": "acme",
                "started_at": "2026-04-01T00:00:00Z",
                "ended_at": None,
                "cost": 0.0,
                "rc": 0,
            },
        )
        await replay.bulk_write_events(
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
        return [("acme", 1)]

    worker = RetentionWorker(storage, retention_provider=provider)
    await worker.tick_now()

    async with storage._conn.session() as db:
        kept = await replay.read_full_replay(db, "in-flight-1")
    assert len(kept) == 1


async def test_idempotent_across_ticks(storage) -> None:
    await _seed_session(storage, "old-1", "acme", ended_at_offset_days=10)

    async def provider():
        return [("acme", 5)]

    worker = RetentionWorker(storage, retention_provider=provider)
    first = await worker.tick_now()
    second = await worker.tick_now()
    assert first["acme"] >= 1
    assert second["acme"] == 0


async def test_invalid_retention_days_falls_back_to_default(storage) -> None:
    await _seed_session(storage, "old-1", "acme", ended_at_offset_days=100)

    async def provider():
        return [("acme", 0)]

    worker = RetentionWorker(
        storage,
        retention_provider=provider,
        default_retention_days=90,
    )
    deletes = await worker.tick_now()
    assert deletes["acme"] >= 1
