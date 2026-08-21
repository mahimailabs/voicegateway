"""Cost can be asked per TURN, not only per model, project or session.

"That turn took four seconds" and "that turn cost this much" were two
questions about the same moment that could not be asked together, so an
expensive turn and a slow turn could never be shown to be the same one.

Every input already existed: ``requests`` carried ``session_id`` and ``turns``
carried ``session_id`` + ``turn_index``. What was missing is the tie between
one CALL and one TURN, and nothing downstream can reconstruct it. The
correlation exists only at the instant the metric fires, while the tracker
still knows which turn is open, which is why the stamping has to happen in
capture and cannot be a later join.

TWO HONESTY PROPERTIES ARE PINNED HERE.

``turn_index`` is absent, never 0, when no turn is tracked. Pipecat sessions
and agents with turn capture off have no turn for a call to belong to, and 0
would claim the first turn for every row they ever write.

Cost that belongs to no turn is REPORTED, not dropped and not folded in. A
session-close reconcile row spans the whole call by construction, so a
per-turn view that presented itself as complete would understate the session
in exactly the case an operator most needs to see.
"""

from __future__ import annotations

import time
import uuid

import pytest

from voicegateway.models.request_model import RequestRecord
from voicegateway.repository.cost_repository import (
    get_cost_by_turn,
    get_unattributed_cost,
)


def _record(session_id: str, *, turn: int | None, cost: float, modality: str = "llm"):
    return RequestRecord(
        id=str(uuid.uuid4()),
        timestamp=time.time(),
        project="default",
        modality=modality,
        model_id=f"openai/{modality}-model",
        provider="openai",
        input_units=100.0,
        cost_usd=cost,
        rated_price_usd=cost,
        session_id=session_id,
        turn_index=turn,
    )


@pytest.fixture
async def store(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "turns.db"))
    from voicegateway.services.storage_service import StorageService

    storage = StorageService(db_path=str(tmp_path / "turns.db"))
    await storage._ensure_initialized()
    return storage


async def _rows(storage, session_id: str):
    async with storage._conn.session() as db:
        return (
            await get_cost_by_turn(db, session_id=session_id),
            await get_unattributed_cost(db, session_id=session_id),
        )


async def test_cost_rolls_up_per_turn(store) -> None:
    sid = "sess-a"
    for rec in (
        _record(sid, turn=0, cost=0.01, modality="stt"),
        _record(sid, turn=0, cost=0.05, modality="llm"),
        _record(sid, turn=1, cost=0.30, modality="llm"),
    ):
        await store.log_request(rec)

    turns, _ = await _rows(store, sid)
    assert [t["turn_index"] for t in turns] == [0, 1]
    assert turns[0]["cost_usd"] == pytest.approx(0.06)
    assert turns[0]["requests"] == 2
    assert turns[1]["cost_usd"] == pytest.approx(0.30)
    # Which turn was expensive is now answerable, which is the whole point.
    assert max(turns, key=lambda t: t["cost_usd"])["turn_index"] == 1


async def test_a_turn_reports_which_modalities_it_spent_on(store) -> None:
    """A turn's cost is only actionable if you can see where it went."""
    sid = "sess-b"
    await store.log_request(_record(sid, turn=0, cost=0.01, modality="stt"))
    await store.log_request(_record(sid, turn=0, cost=0.05, modality="llm"))
    turns, _ = await _rows(store, sid)
    assert turns[0]["modalities"] == ["llm", "stt"]


async def test_untracked_calls_are_reported_not_dropped(store) -> None:
    """The property that keeps a per-turn total from silently under-counting.

    A session-close reconcile row has no turn by construction. Dropping it
    would make the per-turn view disagree with the session total with nothing
    saying why.
    """
    sid = "sess-c"
    await store.log_request(_record(sid, turn=0, cost=0.05))
    await store.log_request(_record(sid, turn=None, cost=0.02))

    turns, unattributed = await _rows(store, sid)
    assert [t["turn_index"] for t in turns] == [0]
    assert unattributed["requests"] == 1
    assert unattributed["cost_usd"] == pytest.approx(0.02)
    # Per-turn plus unattributed reconstructs the session total exactly.
    total = sum(t["cost_usd"] for t in turns) + unattributed["cost_usd"]
    assert total == pytest.approx(0.07)


async def test_an_untracked_call_is_null_rather_than_turn_zero(store) -> None:
    """0 would claim the first turn for every Pipecat row ever written."""
    sid = "sess-d"
    await store.log_request(_record(sid, turn=None, cost=0.02))
    turns, unattributed = await _rows(store, sid)
    assert turns == []
    assert unattributed["requests"] == 1


async def test_turns_do_not_leak_across_sessions(store) -> None:
    """``turn_index`` only means something inside one session.

    Turn 3 of two different calls are unrelated, so a query that forgot to
    scope by session would sum strangers together and report a plausible
    number.
    """
    await store.log_request(_record("sess-e", turn=0, cost=0.05))
    await store.log_request(_record("sess-f", turn=0, cost=0.99))
    turns, _ = await _rows(store, "sess-e")
    assert len(turns) == 1
    assert turns[0]["cost_usd"] == pytest.approx(0.05)


async def test_the_column_survives_the_write_and_read_round_trip(store) -> None:
    """The failure mode this repo has already had once.

    ``revision`` was added to the model and the migration and left out of
    ``_INSERT_REQUEST``, so it was accepted everywhere and stored nowhere.
    Only a read-back catches that, so this reads the row back rather than
    trusting the write.
    """
    sid = "sess-g"
    await store.log_request(_record(sid, turn=7, cost=0.05))
    rows = await store.get_requests_in_window()
    stored = [r for r in rows if r.get("session_id") == sid]
    assert stored, "the row was not written at all"
    assert stored[0]["turn_index"] == 7


async def test_revision_is_readable_on_a_row_not_only_in_aggregate(store) -> None:
    """The gap the read-column guard was added for.

    ``revision`` stored correctly and no row reader returned it, so
    ``get_cost_by_revision`` could total two revisions apart while nothing
    could tell you which individual requests belonged to either. Written and
    unreadable looks, to a caller, exactly like never added.
    """
    rec = _record("sess-rev", turn=0, cost=0.05)
    rec.revision = "deploy-42"
    await store.log_request(rec)

    rows = await store.get_requests_in_window()
    stored = [r for r in rows if r.get("session_id") == "sess-rev"]
    assert stored, "the row was not written at all"
    assert stored[0]["revision"] == "deploy-42"
