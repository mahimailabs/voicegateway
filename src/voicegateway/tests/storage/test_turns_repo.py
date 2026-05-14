"""Contract tests for voicegateway.repository.turns (T05)."""

from __future__ import annotations

from voicegateway.middleware.turn_tracker import TurnRow
from voicegateway.repository import turns_repository as turns
from voicegateway.storage.sqlite import SQLiteStorage


def _row(
    session_id: str,
    turn_index: int,
    caller_start: int,
    caller_end: int,
    agent_start: int | None = None,
    agent_end: int | None = None,
    response_speed: int | None = None,
) -> TurnRow:
    return TurnRow(
        session_id=session_id,
        turn_index=turn_index,
        caller_speak_start_ms=caller_start,
        caller_speak_end_ms=caller_end,
        agent_speak_start_ms=agent_start,
        agent_speak_end_ms=agent_end,
        response_speed_ms=response_speed,
    )


async def _fresh_storage(tmp_path) -> SQLiteStorage:
    storage = SQLiteStorage(str(tmp_path / "turns.db"))
    await storage._ensure_initialized()
    return storage


async def test_create_turn_and_list_round_trip(tmp_path) -> None:
    storage = await _fresh_storage(tmp_path)
    async with storage._conn.session() as db:
        t = _row("s1", 0, 1000, 2000, 2200, 4000, 200)
        await turns.create_turn(db, t)

        listed = await turns.list_turns_by_session(db, "s1")
        assert len(listed) == 1
        assert listed[0] == t


async def test_create_turns_bulk(tmp_path) -> None:
    storage = await _fresh_storage(tmp_path)
    async with storage._conn.session() as db:
        rows = [
            _row("s1", i, i * 1000, i * 1000 + 500, response_speed=100 + i)
            for i in range(5)
        ]
        n = await turns.create_turns_bulk(db, rows)
        assert n == 5

        listed = await turns.list_turns_by_session(db, "s1")
        assert len(listed) == 5
        assert [r.turn_index for r in listed] == [0, 1, 2, 3, 4]


async def test_create_turns_bulk_empty_is_noop(tmp_path) -> None:
    storage = await _fresh_storage(tmp_path)
    async with storage._conn.session() as db:
        n = await turns.create_turns_bulk(db, [])
        assert n == 0


async def test_aggregate_response_speed_returns_percentiles(tmp_path) -> None:
    storage = await _fresh_storage(tmp_path)
    async with storage._conn.session() as db:
        rows = [_row("s1", i, 0, 0, 0, 0, response_speed=i + 1) for i in range(100)]
        await turns.create_turns_bulk(db, rows)

        agg = await turns.aggregate_response_speed(db, "s1")
        assert agg["p50_ms"] is not None
        assert agg["p95_ms"] is not None
        assert agg["p99_ms"] is not None
        assert agg["p50_ms"] < agg["p95_ms"] < agg["p99_ms"]


async def test_aggregate_response_speed_empty_returns_nulls(tmp_path) -> None:
    storage = await _fresh_storage(tmp_path)
    async with storage._conn.session() as db:
        agg = await turns.aggregate_response_speed(db, "no-such-session")
        assert agg == {"p50_ms": None, "p95_ms": None, "p99_ms": None}


async def test_aggregate_response_speed_single_value(tmp_path) -> None:
    storage = await _fresh_storage(tmp_path)
    async with storage._conn.session() as db:
        await turns.create_turn(db, _row("s1", 0, 0, 0, 0, 0, response_speed=250))
        agg = await turns.aggregate_response_speed(db, "s1")
        assert agg == {"p50_ms": 250, "p95_ms": 250, "p99_ms": 250}


async def test_count_overlap_turns(tmp_path) -> None:
    storage = await _fresh_storage(tmp_path)
    async with storage._conn.session() as db:
        await turns.create_turns_bulk(
            db,
            [
                _row("s1", 0, 0, 500, 500, 2000, 0),
                _row("s1", 1, 1500, 2500, 2500, 3000, 0),
                _row("s1", 2, 3500, 4000, 4000, 5000, 0),
            ],
        )
        count = await turns.count_overlap_turns(db, "s1")
        assert count == 1


async def test_count_overlap_excludes_null_agent_end(tmp_path) -> None:
    storage = await _fresh_storage(tmp_path)
    async with storage._conn.session() as db:
        await turns.create_turns_bulk(
            db,
            [
                _row("s1", 0, 0, 500, 500, None, None),
                _row("s1", 1, 100, 1000, 1000, 2000, 0),
            ],
        )
        count = await turns.count_overlap_turns(db, "s1")
        assert count == 0
