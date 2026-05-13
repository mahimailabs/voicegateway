"""Contract tests for voicegateway.repository.dead_air (T06)."""

from __future__ import annotations

import aiosqlite

from voicegateway.middleware.dead_air_detector import DeadAirEvent
from voicegateway.repository import dead_air_repository as dead_air
from voicegateway.storage.sqlite import SQLiteStorage


async def _fresh_db(tmp_path) -> str:
    db_path = str(tmp_path / "dead_air.db")
    storage = SQLiteStorage(db_path)
    await storage._ensure_initialized()
    return db_path


async def test_create_event_round_trip(tmp_path) -> None:
    db_path = await _fresh_db(tmp_path)
    async with aiosqlite.connect(db_path) as db:
        event = DeadAirEvent(
            session_id="s1",
            started_at_ms=1000,
            duration_ms=3500,
            threshold_used_ms=3000,
        )
        await dead_air.create_event(db, event)

        listed = await dead_air.list_events_by_session(db, "s1")
        assert len(listed) == 1
        assert listed[0] == event


async def test_list_events_ordered_by_started_at_asc(tmp_path) -> None:
    db_path = await _fresh_db(tmp_path)
    async with aiosqlite.connect(db_path) as db:
        events = [
            DeadAirEvent("s1", 3000, 100, 50),
            DeadAirEvent("s1", 1000, 100, 50),
            DeadAirEvent("s1", 2000, 100, 50),
        ]
        for e in events:
            await dead_air.create_event(db, e)

        listed = await dead_air.list_events_by_session(db, "s1")
        assert [e.started_at_ms for e in listed] == [1000, 2000, 3000]


async def test_count_events_by_session(tmp_path) -> None:
    db_path = await _fresh_db(tmp_path)
    async with aiosqlite.connect(db_path) as db:
        for i in range(4):
            await dead_air.create_event(db, DeadAirEvent("s1", i * 1000, 100, 50))
        await dead_air.create_event(db, DeadAirEvent("s2", 0, 100, 50))

        assert await dead_air.count_events_by_filter(db, "s1") == 4
        assert await dead_air.count_events_by_filter(db, "s2") == 1
        # No filter = total
        assert await dead_air.count_events_by_filter(db) == 5


async def test_count_events_by_time_range(tmp_path) -> None:
    db_path = await _fresh_db(tmp_path)
    async with aiosqlite.connect(db_path) as db:
        for i in range(5):
            await dead_air.create_event(db, DeadAirEvent("s1", i * 1000, 100, 50))

        # [2000, 4000) → started_at_ms in {2000, 3000} → 2 events.
        n = await dead_air.count_events_by_filter(
            db, started_after_ms=2000, started_before_ms=4000
        )
        assert n == 2


async def test_list_events_unknown_session_returns_empty(tmp_path) -> None:
    db_path = await _fresh_db(tmp_path)
    async with aiosqlite.connect(db_path) as db:
        assert await dead_air.list_events_by_session(db, "ghost") == []
