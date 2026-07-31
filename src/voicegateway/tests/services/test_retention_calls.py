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


_INSERT_SESSION = text(
    "INSERT INTO sessions "
    "(id, project, started_at, ended_at, total_cost_usd, request_count, "
    " tenant_id, room_name, call_id) "
    "VALUES (:id, :project, :at, :at, 0.0, 0, :tenant, :room, :call_id)"
)


async def _seed_session(
    storage,
    session_id: str,
    project: str,
    call_id: str | None,
    tenant: str | None = None,
    ended_days_ago: float = 1.0,
) -> None:
    """Insert a session that survives the session pass but names a call.

    ``ended_days_ago`` defaults inside every retention window used here, so the
    session itself is never the thing being pruned: the call is.
    """
    at = (datetime.now(UTC) - timedelta(days=ended_days_ago)).isoformat()
    async with storage._conn.session() as db:
        await db.execute(
            _INSERT_SESSION,
            {
                "id": session_id,
                "project": project,
                "at": at,
                "tenant": tenant,
                "room": f"room-{session_id}",
                "call_id": call_id,
            },
        )
        await db.commit()


async def _session_call_id(storage, session_id: str) -> str | None:
    """The stored pointer. Asserts the row still exists, so a nulled pointer is
    never confused with a deleted session."""
    async with storage._conn.session() as db:
        row = (
            await db.execute(
                text("SELECT call_id FROM sessions WHERE id = :i"), {"i": session_id}
            )
        ).fetchone()
    assert row is not None, f"session {session_id} was deleted, not repointed"
    return row[0]


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


# ---- sessions.call_id must not outlive the call it names -------------------
#
# A session prunes on its own clock and routinely outlives the call, so the
# prune above would otherwise leave the pointer aimed at a deleted row. The
# read side (session_repository.read_correlation_rate) also guards this with a
# LEFT JOIN and keeps doing so; these tests pin the source-side half.


async def test_pruned_call_nulls_the_session_pointer(storage) -> None:
    old = await storage.upsert_call(
        origin="webhook",
        room_sid="RM_ptr_old",
        project="acme",
        started_at_ms=_ms_days_ago(11),
        ended_at_ms=_ms_days_ago(10),
    )
    await _seed_session(storage, "sess-old", "acme", call_id=old)

    await RetentionWorker(storage, retention_provider=_provider("acme", 5)).tick_now()

    assert await _count(storage, "calls", "id = :i", {"i": old}) == 0
    assert await _session_call_id(storage, "sess-old") is None


async def test_surviving_call_keeps_the_session_pointer(storage) -> None:
    """Only pointers at deleted calls get nulled. A live join is not collateral."""
    young = await storage.upsert_call(
        origin="webhook",
        room_sid="RM_ptr_young",
        project="acme",
        started_at_ms=_ms_days_ago(1),
        ended_at_ms=_ms_days_ago(1),
    )
    await _seed_session(storage, "sess-young", "acme", call_id=young)

    await RetentionWorker(storage, retention_provider=_provider("acme", 5)).tick_now()

    assert await _count(storage, "calls", "id = :i", {"i": young}) == 1
    assert await _session_call_id(storage, "sess-young") == young


async def test_other_calls_projects_and_tenants_are_unaffected(storage) -> None:
    """The null-out follows the deleted ids, not the project or the tenant."""
    doomed = await storage.upsert_call(
        origin="webhook",
        room_sid="RM_doomed",
        project="acme",
        ended_at_ms=_ms_days_ago(10),
    )
    survivor = await storage.upsert_call(
        origin="webhook",
        room_sid="RM_survivor",
        project="acme",
        ended_at_ms=_ms_days_ago(1),
    )
    # Aged, but its project is not in this tick's provider, so it stays.
    foreign = await storage.upsert_call(
        origin="webhook",
        room_sid="RM_foreign",
        project="other",
        ended_at_ms=_ms_days_ago(10),
    )
    await _seed_session(storage, "s-doomed", "acme", call_id=doomed, tenant="t1")
    await _seed_session(storage, "s-survivor", "acme", call_id=survivor, tenant="t2")
    await _seed_session(storage, "s-foreign", "other", call_id=foreign, tenant="t3")
    await _seed_session(storage, "s-none", "acme", call_id=None, tenant="t2")

    await RetentionWorker(storage, retention_provider=_provider("acme", 5)).tick_now()

    assert await _session_call_id(storage, "s-doomed") is None
    assert await _session_call_id(storage, "s-survivor") == survivor
    assert await _session_call_id(storage, "s-foreign") == foreign
    assert await _session_call_id(storage, "s-none") is None
    assert await _count(storage, "calls", "id = :i", {"i": foreign}) == 1


class _CommitFails:
    """AsyncSession stand-in that forwards statements but refuses to commit.

    Stands in for a crash at the chunk boundary. Before raising it snapshots the
    state on its OWN connection, where both the null-out and the delete are
    already pending: that snapshot is what proves they share a transaction
    rather than merely both happening at some point.
    """

    def __init__(self, db, call_id: str, session_id: str) -> None:
        self._db = db
        self._call_id = call_id
        self._session_id = session_id
        self.pending: tuple[int, str | None] | None = None

    async def execute(self, *args, **kwargs):
        return await self._db.execute(*args, **kwargs)

    async def commit(self) -> None:
        calls_left = int(
            (
                await self._db.execute(
                    text("SELECT COUNT(*) FROM calls WHERE id = :i"),
                    {"i": self._call_id},
                )
            ).scalar()
            or 0
        )
        pointer = (
            await self._db.execute(
                text("SELECT call_id FROM sessions WHERE id = :i"),
                {"i": self._session_id},
            )
        ).scalar()
        self.pending = (calls_left, pointer)
        raise RuntimeError("commit boom")


async def test_null_out_and_delete_commit_together(storage) -> None:
    """Neither half lands on its own: the pointer and the row share one commit."""
    doomed = await storage.upsert_call(
        origin="webhook",
        room_sid="RM_atomic",
        project="acme",
        started_at_ms=_ms_days_ago(11),
        ended_at_ms=_ms_days_ago(10),
    )
    await storage.upsert_call_leg(call_id=doomed, participant_sid="PA_atomic")
    await _seed_session(storage, "sess-atomic", "acme", call_id=doomed)

    worker = RetentionWorker(storage, retention_provider=_provider("acme", 5))
    cutoff_ts = (datetime.now(UTC) - timedelta(days=5)).timestamp()
    # The context manager rolls back on the way out, which is exactly what the
    # worker's own tick does when a pass raises.
    failing: _CommitFails | None = None
    with pytest.raises(RuntimeError, match="commit boom"):
        async with storage._conn.session() as db:
            failing = _CommitFails(db, call_id=doomed, session_id="sess-atomic")
            await worker._prune_calls(failing, "acme", cutoff_ts)

    # Uncommitted, both halves were staged together...
    assert failing is not None
    assert failing.pending == (0, None)
    # ...and the rollback took both back.
    assert await _count(storage, "calls", "id = :i", {"i": doomed}) == 1
    assert await _count(storage, "call_legs", "call_id = :i", {"i": doomed}) == 1
    assert await _session_call_id(storage, "sess-atomic") == doomed
