"""Phase 3, Step 3: retention prunes requests, sessions, and dependents.

Extends the replay-only RetentionWorker: aged sessions and their dependent
rows (replay, turns, dead-air, guardrail) prune by ``ended_at``; requests
prune independently by ``timestamp`` (a request may have no session).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from voicegateway.models.request_model import RequestRecord
from voicegateway.services.retention_service import RetentionWorker
from voicegateway.services.storage_service import StorageService

_INSERT_SESSION = text(
    "INSERT INTO sessions "
    "(id, project, started_at, ended_at, total_cost_usd, request_count) "
    "VALUES (:id, :project, :started_at, :ended_at, :cost, :rc)"
)


@pytest.fixture
async def storage(tmp_path):
    s = StorageService(str(tmp_path / "retention.db"))
    await s._ensure_initialized()
    return s


def _provider(project: str, days: int):
    async def provider():
        return [(project, days)]

    return provider


async def _seed_session(storage, sid: str, project: str, ended_days_ago: float) -> None:
    ended = (datetime.now(UTC) - timedelta(days=ended_days_ago)).isoformat()
    async with storage._conn.session() as db:
        await db.execute(
            _INSERT_SESSION,
            {
                "id": sid,
                "project": project,
                "started_at": ended,
                "ended_at": ended,
                "cost": 0.0,
                "rc": 0,
            },
        )
        await db.commit()


async def _seed_request(
    storage, rid: str, project: str, days_ago: float, session_id: str | None
) -> None:
    ts = (datetime.now(UTC) - timedelta(days=days_ago)).timestamp()
    await storage.log_request(
        RequestRecord(
            id=rid,
            timestamp=ts,
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            project=project,
            session_id=session_id,
        )
    )


async def _count(storage, table: str, where: str, params: dict) -> int:
    async with storage._conn.session() as db:
        result = await db.execute(
            text(f"SELECT COUNT(*) FROM {table} WHERE {where}"), params
        )
        return int(result.scalar() or 0)


async def test_prune_deletes_aged_session_rows(storage) -> None:
    await _seed_session(storage, "old", "acme", ended_days_ago=10)
    await _seed_session(storage, "young", "acme", ended_days_ago=1)
    await RetentionWorker(storage, retention_provider=_provider("acme", 5)).tick_now()
    assert await _count(storage, "sessions", "id = :i", {"i": "old"}) == 0
    assert await _count(storage, "sessions", "id = :i", {"i": "young"}) == 1


async def test_session_less_request_pruned_by_timestamp(storage) -> None:
    await _seed_request(storage, "r-old", "acme", days_ago=10, session_id=None)
    await _seed_request(storage, "r-new", "acme", days_ago=1, session_id=None)
    await RetentionWorker(storage, retention_provider=_provider("acme", 5)).tick_now()
    assert await _count(storage, "requests", "id = :i", {"i": "r-old"}) == 0
    assert await _count(storage, "requests", "id = :i", {"i": "r-new"}) == 1


async def test_recent_request_survives_even_when_its_session_is_aged(storage) -> None:
    # Independent clocks: the session is aged (pruned) but the request is recent.
    # The request creates the session via upsert, so age the session afterward.
    await _seed_request(storage, "r-recent", "acme", days_ago=1, session_id="s-aged")
    aged = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    async with storage._conn.session() as db:
        await db.execute(
            text("UPDATE sessions SET ended_at = :e, project = 'acme' WHERE id = :i"),
            {"e": aged, "i": "s-aged"},
        )
        await db.commit()
    await RetentionWorker(storage, retention_provider=_provider("acme", 5)).tick_now()
    assert await _count(storage, "sessions", "id = :i", {"i": "s-aged"}) == 0
    assert await _count(storage, "requests", "id = :i", {"i": "r-recent"}) == 1


async def test_prune_deletes_session_dependents(storage) -> None:
    await _seed_session(storage, "s-old", "acme", ended_days_ago=10)
    async with storage._conn.session() as db:
        await db.execute(
            text(
                "INSERT INTO turns "
                "(session_id, turn_index, caller_speak_start_ms, caller_speak_end_ms) "
                "VALUES ('s-old', 0, 0, 100)"
            )
        )
        await db.execute(
            text(
                "INSERT INTO dead_air_events "
                "(session_id, started_at_ms, duration_ms, threshold_used_ms) "
                "VALUES ('s-old', 0, 100, 3000)"
            )
        )
        await db.execute(
            text(
                "INSERT INTO guardrail_events (event_type, session_id) "
                "VALUES ('fired', 's-old')"
            )
        )
        await db.commit()
    await RetentionWorker(storage, retention_provider=_provider("acme", 5)).tick_now()
    for table in ("turns", "dead_air_events", "guardrail_events"):
        assert await _count(storage, table, "session_id = :s", {"s": "s-old"}) == 0


async def test_request_prune_is_batched(storage) -> None:
    for i in range(120):
        await _seed_request(storage, f"r{i}", "acme", days_ago=10, session_id=None)
    worker = RetentionWorker(
        storage, retention_provider=_provider("acme", 5), batch_size=50
    )
    await worker.tick_now()
    assert await _count(storage, "requests", "project = :p", {"p": "acme"}) == 0
