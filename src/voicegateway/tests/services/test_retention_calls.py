"""Calls and their legs must age out, on their own clock.

A call can exist with no session at all (that is the whole reason the table
exists), so it cannot ride the session pass. It also stores epoch milliseconds
while the rest of the worker prunes in ISO strings and epoch seconds, which is
the specific thing these tests pin down.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from voicegateway.services.retention_service import (
    _CALL_CHILD_TABLES,
    RetentionWorker,
)
from voicegateway.services.storage_service import StorageService


@pytest.fixture
async def storage(tmp_path):
    s = StorageService(str(tmp_path / "retention-calls.db"))
    await s._ensure_initialized()
    return s


def _provider(project: str, days: int):
    async def provider():
        return [(project, days)]

    return provider


def _ms_days_ago(days: float) -> int:
    return int((datetime.now(UTC) - timedelta(days=days)).timestamp() * 1000)


async def _count(storage, table: str, where: str, params: dict) -> int:
    async with storage._conn.session() as db:
        result = await db.execute(
            text(f"SELECT COUNT(*) FROM {table} WHERE {where}"), params
        )
        return int(result.scalar() or 0)


def test_call_legs_is_a_call_child_table() -> None:
    assert "call_legs" in _CALL_CHILD_TABLES


async def test_aged_call_and_its_legs_are_deleted(storage) -> None:
    old = await storage.upsert_call(
        origin="webhook",
        room_sid="RM_old",
        project="acme",
        started_at_ms=_ms_days_ago(11),
        ended_at_ms=_ms_days_ago(10),
    )
    young = await storage.upsert_call(
        origin="webhook",
        room_sid="RM_young",
        project="acme",
        started_at_ms=_ms_days_ago(1),
        ended_at_ms=_ms_days_ago(1),
    )
    for call_id in (old, young):
        await storage.upsert_call_leg(
            call_id=call_id, participant_sid=f"PA_{call_id[:6]}", kind="SIP"
        )

    await RetentionWorker(storage, retention_provider=_provider("acme", 5)).tick_now()

    assert await _count(storage, "calls", "id = :i", {"i": old}) == 0
    assert await _count(storage, "call_legs", "call_id = :i", {"i": old}) == 0
    assert await _count(storage, "calls", "id = :i", {"i": young}) == 1
    assert await _count(storage, "call_legs", "call_id = :i", {"i": young}) == 1


async def test_a_call_that_never_ended_still_ages_out(storage) -> None:
    """Ending webhooks get lost; a start-only row must not live forever."""
    stuck = await storage.upsert_call(
        origin="webhook",
        room_sid="RM_stuck",
        project="acme",
        started_at_ms=_ms_days_ago(30),
    )
    await RetentionWorker(storage, retention_provider=_provider("acme", 5)).tick_now()
    assert await _count(storage, "calls", "id = :i", {"i": stuck}) == 0


async def test_a_call_with_no_timestamps_is_left_alone(storage) -> None:
    """No timestamp means no age. Guessing one would delete a live call."""
    timeless = await storage.upsert_call(
        origin="loadgen", attempt_id="att-timeless", project="acme"
    )
    await RetentionWorker(storage, retention_provider=_provider("acme", 5)).tick_now()
    assert await _count(storage, "calls", "id = :i", {"i": timeless}) == 1


async def test_another_projects_calls_are_untouched(storage) -> None:
    mine = await storage.upsert_call(
        origin="webhook",
        room_sid="RM_mine",
        project="acme",
        ended_at_ms=_ms_days_ago(10),
    )
    theirs = await storage.upsert_call(
        origin="webhook",
        room_sid="RM_theirs",
        project="other",
        ended_at_ms=_ms_days_ago(10),
    )
    await RetentionWorker(storage, retention_provider=_provider("acme", 5)).tick_now()
    assert await _count(storage, "calls", "id = :i", {"i": mine}) == 0
    assert await _count(storage, "calls", "id = :i", {"i": theirs}) == 1


async def test_call_prune_is_idempotent_and_batched(storage) -> None:
    for i in range(120):
        call_id = await storage.upsert_call(
            origin="loadgen",
            attempt_id=f"att-{i}",
            project="acme",
            started_at_ms=_ms_days_ago(10),
        )
        await storage.upsert_call_leg(call_id=call_id, participant_sid="PA_1")
    worker = RetentionWorker(
        storage, retention_provider=_provider("acme", 5), batch_size=50
    )
    first = await worker.tick_now()
    second = await worker.tick_now()
    assert first["acme"] >= 240  # 120 calls + 120 legs
    assert second["acme"] == 0
    assert await _count(storage, "calls", "project = :p", {"p": "acme"}) == 0
    assert await _count(storage, "call_legs", "1 = :one", {"one": 1}) == 0
