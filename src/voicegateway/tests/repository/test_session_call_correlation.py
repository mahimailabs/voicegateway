"""sessions <-> calls correlation: the join, and the number that watches it.

Two things are under test here, and the second exists because the first fails
SILENTLY:

* **the join** -- ``sessions.room_name`` is stamped from ``metadata["room"]`` and
  ``sessions.call_id`` is resolved from it, forward-only, refusing to guess when
  a room name matches more than one call (room names are not unique; ``calls``
  keys on ``room_sid`` for exactly that reason);
* **the correlation rate** -- whose denominator is *sessions that had a room and
  should have joined*, not all sessions. A web or Pipecat session has no room and
  can never join, so counting it as a failure would peg the number low forever
  and train everyone to ignore it.

A dangling ``call_id`` (the call was pruned, the session was not) is also not a
correlation, and is asserted here as such.
"""

from __future__ import annotations

import sqlite3
import time
import uuid

import pytest

from voicegateway.models.request_model import RequestRecord
from voicegateway.repository import request_log_repository as reqlog
from voicegateway.repository import session_repository as sessions
from voicegateway.services.storage_service import StorageService

_ROOM = "sip-call-7f3a"


def _read_session(db_path: str, sid: str) -> tuple[str | None, str | None] | None:
    """``(room_name, call_id)`` straight from SQLite, or None if no row."""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT room_name, call_id FROM sessions WHERE id = ?", (sid,)
        ).fetchone()
    finally:
        conn.close()
    return None if row is None else (row[0], row[1])


def _record(
    *,
    session_id: str,
    room: str | None = None,
    project: str = "tony-pizza",
    modality: str = "llm",
    ts: float | None = None,
) -> RequestRecord:
    metadata: dict[str, object] = {}
    if room is not None:
        metadata["room"] = room
    return RequestRecord(
        id=str(uuid.uuid4()),
        timestamp=ts if ts is not None else time.time(),
        modality=modality,
        model_id=f"openai/{modality}-test",
        provider="openai",
        project=project,
        cost_usd=0.001,
        session_id=session_id,
        metadata=metadata,
    )


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "correlation.db")


@pytest.fixture
async def storage(db_path):
    svc = StorageService(db_path)
    try:
        yield svc
    finally:
        await svc.aclose()


async def _seed_call(
    storage: StorageService, *, room_sid: str, room_name: str | None = _ROOM
) -> str:
    """One webhook-originated call row, as the receiver would write it."""
    return await storage.upsert_call(
        origin="webhook",
        room_sid=room_sid,
        room_name=room_name,
        project="tony-pizza",
        started_at_ms=1_800_000_000_000,
    )


# ---------------------------------------------------------------------------
# The join key: sessions.room_name
# ---------------------------------------------------------------------------


async def test_room_name_is_stamped_from_request_metadata(storage, db_path):
    sid = "vg-room-stamped"
    await storage.log_request(_record(session_id=sid, room=_ROOM))

    room_name, call_id = _read_session(db_path, sid)
    assert room_name == _ROOM
    # No call row exists, so the pointer stays NULL rather than guessing.
    assert call_id is None


async def test_session_without_a_job_context_has_no_room_name(storage, db_path):
    """Web and Pipecat sessions carry no room. NULL is correct, not a failure."""
    sid = "vg-no-room"
    await storage.log_request(_record(session_id=sid, room=None))

    assert _read_session(db_path, sid) == (None, None)


async def test_empty_room_metadata_is_not_a_room(storage, db_path):
    """An empty string is not a room anyone can join to; storing it would put
    the session in the eligible denominator with no way ever to leave it."""
    sid = "vg-empty-room"
    await storage.log_request(_record(session_id=sid, room=""))

    assert _read_session(db_path, sid) == (None, None)


async def test_room_name_survives_a_later_request_that_lost_the_context(
    storage, db_path
):
    """COALESCE, not last-write-wins: one request without the context must not
    erase the join key another request established."""
    sid = "vg-room-preserved"
    await storage.log_request(_record(session_id=sid, room=_ROOM))
    await storage.log_request(_record(session_id=sid, room=None))

    room_name, _ = _read_session(db_path, sid)
    assert room_name == _ROOM


# ---------------------------------------------------------------------------
# The pointer: sessions.call_id, and the 0 / 1 / many cardinalities
# ---------------------------------------------------------------------------


async def test_exactly_one_matching_call_resolves_the_pointer(storage, db_path):
    call_id = await _seed_call(storage, room_sid="RM_one")
    sid = "vg-one-match"
    await storage.log_request(_record(session_id=sid, room=_ROOM))

    assert _read_session(db_path, sid) == (_ROOM, call_id)


async def test_no_matching_call_resolves_on_the_next_request(storage, db_path):
    """The first request of a call routinely beats the room_started webhook.

    Forward-only means the retry happens on the session's next request, not in a
    backfill: an older session has nothing to join to, so a repair pass would
    either find nothing or invent a link.
    """
    sid = "vg-late-webhook"
    await storage.log_request(_record(session_id=sid, room=_ROOM))
    assert _read_session(db_path, sid) == (_ROOM, None)

    call_id = await _seed_call(storage, room_sid="RM_late")
    await storage.log_request(_record(session_id=sid, room=_ROOM))

    assert _read_session(db_path, sid) == (_ROOM, call_id)


async def test_a_room_matching_two_calls_is_refused_not_guessed(
    storage, db_path, caplog
):
    """A deployment pinning one room name makes the key ambiguous. Picking one
    call would file the session's cost and latency under the wrong call, and
    nothing downstream could tell."""
    sessions._warn_ambiguous_room.cache_clear()
    await _seed_call(storage, room_sid="RM_a")
    await _seed_call(storage, room_sid="RM_b")

    sid = "vg-ambiguous"
    with caplog.at_level("WARNING", logger=sessions.__name__):
        await storage.log_request(_record(session_id=sid, room=_ROOM))

    assert _read_session(db_path, sid) == (_ROOM, None)
    assert any(_ROOM in r.getMessage() for r in caplog.records)


async def test_the_ambiguity_warning_does_not_repeat_per_request(storage, caplog):
    """An ambiguous room is a permanent property of the deployment, so warning
    per request would flood the log of the load test this exists to measure."""
    sessions._warn_ambiguous_room.cache_clear()
    await _seed_call(storage, room_sid="RM_a")
    await _seed_call(storage, room_sid="RM_b")

    with caplog.at_level("WARNING", logger=sessions.__name__):
        for i in range(4):
            await storage.log_request(_record(session_id=f"vg-flood-{i}", room=_ROOM))

    warnings = [r for r in caplog.records if _ROOM in r.getMessage()]
    assert len(warnings) == 1


async def test_the_first_resolution_wins_when_a_second_call_takes_the_room(
    storage, db_path
):
    """Resolved unambiguously at the time, so a later collision cannot re-point
    a session that already names its call."""
    first = await _seed_call(storage, room_sid="RM_first")
    sid = "vg-stable-pointer"
    await storage.log_request(_record(session_id=sid, room=_ROOM))
    assert _read_session(db_path, sid) == (_ROOM, first)

    await _seed_call(storage, room_sid="RM_second")
    await storage.log_request(_record(session_id=sid, room=_ROOM))

    assert _read_session(db_path, sid) == (_ROOM, first)


async def test_a_session_with_no_room_never_reaches_the_resolution(
    storage, db_path, monkeypatch
):
    """The resolution is skipped entirely without a room, so a web or Pipecat
    session adds nothing to the per-request hot path."""
    calls: list[str] = []

    async def _spy(db, *, session_id: str, room_name: str) -> str | None:
        calls.append(session_id)
        return None

    monkeypatch.setattr(sessions, "correlate_session_to_call", _spy)
    await _seed_call(storage, room_sid="RM_unrelated")
    await storage.log_request(_record(session_id="vg-skip", room=None))
    await storage.log_request(_record(session_id="vg-has-room", room=_ROOM))

    assert calls == ["vg-has-room"]
    assert _read_session(db_path, "vg-skip") == (None, None)


# ---------------------------------------------------------------------------
# The number: read_correlation_rate
# ---------------------------------------------------------------------------


async def test_rate_is_none_when_nothing_could_have_correlated(storage):
    """Never 100%, never 0%: an empty denominator is not a measurement."""
    await storage.log_request(_record(session_id="vg-web-1", room=None))
    await storage.log_request(_record(session_id="vg-web-2", room=None))

    rate = await storage.read_correlation_rate()
    assert rate["eligible"] == 0
    assert rate["rate"] is None
    assert rate["status"] == "unknown"
    assert rate["no_room"] == 2


async def test_only_sessions_that_had_a_room_are_in_the_denominator(storage):
    await _seed_call(storage, room_sid="RM_ok")
    await storage.log_request(_record(session_id="vg-joined", room=_ROOM))
    await storage.log_request(_record(session_id="vg-orphan", room="room-with-no-call"))
    await storage.log_request(_record(session_id="vg-web", room=None))

    rate = await storage.read_correlation_rate()
    assert rate["eligible"] == 2
    assert rate["correlated"] == 1
    assert rate["rate"] == pytest.approx(0.5)
    assert rate["no_room"] == 1
    assert rate["status"] == "warn"


async def test_a_dangling_pointer_is_not_a_correlation(storage, db_path):
    """``calls`` prune on their own clock, so a session can outlive the call it
    names. The stale pointer must not still be counted as a joined session."""
    await _seed_call(storage, room_sid="RM_pruned")
    await storage.log_request(_record(session_id="vg-dangling", room=_ROOM))
    assert (await storage.read_correlation_rate())["correlated"] == 1

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM calls")
        conn.commit()
    finally:
        conn.close()

    rate = await storage.read_correlation_rate()
    assert rate["eligible"] == 1
    assert rate["correlated"] == 0
    assert rate["dangling"] == 1
    assert rate["rate"] == pytest.approx(0.0)
    assert rate["status"] == "warn"


async def test_ambiguous_sessions_are_counted_separately(storage):
    """The one failure mode a correct webhook pipeline still produces, and it
    needs a different fix than a missing receiver does."""
    sessions._warn_ambiguous_room.cache_clear()
    await _seed_call(storage, room_sid="RM_x")
    await _seed_call(storage, room_sid="RM_y")
    await storage.log_request(_record(session_id="vg-amb", room=_ROOM))
    await storage.log_request(_record(session_id="vg-missing", room="no-such-room"))

    rate = await storage.read_correlation_rate()
    assert rate["eligible"] == 2
    assert rate["correlated"] == 0
    assert rate["ambiguous"] == 1


async def test_probe_sessions_are_excluded_from_the_number(storage):
    """VoiceGateway's own probe rooms are synthetic traffic it created itself;
    every other read surface in the package excludes them too."""
    probe_room = f"{reqlog.PROBE_ROOM_PREFIX}agent-deadbeef"
    await storage.log_request(_record(session_id="vg-probe-sess", room=probe_room))

    rate = await storage.read_correlation_rate()
    assert rate["eligible"] == 0
    assert rate["no_room"] == 0
    assert rate["status"] == "unknown"


async def test_a_rate_on_the_threshold_is_not_a_warning(storage):
    await _seed_call(storage, room_sid="RM_t")
    await storage.log_request(_record(session_id="vg-t-1", room=_ROOM))
    await storage.log_request(_record(session_id="vg-t-2", room="unmatched"))

    at = await storage.read_correlation_rate(warn_threshold=0.5)
    assert at["rate"] == pytest.approx(0.5)
    assert at["warn_threshold"] == pytest.approx(0.5)
    assert at["status"] == "ok"

    above = await storage.read_correlation_rate(warn_threshold=0.51)
    assert above["status"] == "warn"


async def test_the_default_threshold_is_published_with_the_number(storage):
    await storage.log_request(_record(session_id="vg-default-th", room=None))

    rate = await storage.read_correlation_rate()
    assert rate["warn_threshold"] == pytest.approx(sessions.CORRELATION_WARN_THRESHOLD)
    assert rate["status"] in sessions.CORRELATION_STATUSES


async def test_an_impossible_threshold_is_refused(storage):
    with pytest.raises(ValueError, match="warn_threshold"):
        await storage.read_correlation_rate(warn_threshold=1.5)


async def test_the_number_can_be_scoped_to_a_project_and_a_window(storage):
    old = time.time() - 86_400
    await storage.log_request(
        _record(session_id="vg-old", room="old-room", ts=old, project="tony-pizza")
    )
    await storage.log_request(
        _record(session_id="vg-other", room="other-room", project="mama-diner")
    )
    await _seed_call(storage, room_sid="RM_scoped")
    await storage.log_request(_record(session_id="vg-now", room=_ROOM))

    scoped = await storage.read_correlation_rate(project="tony-pizza")
    assert scoped["eligible"] == 2  # the mama-diner session is out

    from datetime import UTC, datetime

    since = datetime.fromtimestamp(time.time() - 3600, tz=UTC).isoformat()
    windowed = await storage.read_correlation_rate(project="tony-pizza", since=since)
    assert windowed["eligible"] == 1  # the day-old session is out
    assert windowed["correlated"] == 1
    assert windowed["rate"] == pytest.approx(1.0)
    assert windowed["status"] == "ok"


# ---------------------------------------------------------------------------
# The Postgres derivation of the sessions UPSERT (§8: string replace, so every
# edit to the SQLite text has to be checked against the derived statement)
# ---------------------------------------------------------------------------


def test_the_postgres_upsert_differs_only_by_instr_to_strpos():
    sqlite_sql = reqlog._UPSERT_SESSION.text
    pg_sql = reqlog._UPSERT_SESSION_PG.text

    assert "INSTR(" not in pg_sql
    assert sqlite_sql.count("INSTR(") == 1
    assert pg_sql.count("STRPOS(") == 1
    # The ONLY divergence between the two dialects, still.
    assert pg_sql.replace("STRPOS(", "INSTR(") == sqlite_sql


def test_the_room_name_clause_survives_the_postgres_derivation():
    for sql in (reqlog._UPSERT_SESSION.text, reqlog._UPSERT_SESSION_PG.text):
        assert ":room_name" in sql
        assert "room_name = COALESCE(sessions.room_name, excluded.room_name)" in sql
