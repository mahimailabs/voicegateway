"""The ``turns`` millisecond columns must be 64-bit, and hold epoch time.

Every turn insert failed on Postgres with "value out of int32 range" because
``Turn`` declared these as plain ``int`` (INTEGER, int32, max 2147483647) while
the values are millisecond timestamps.

The reason it survived is the reason for the first test here: SQLite's dynamic
typing stores an oversized value in an INTEGER column without complaint, so a
round-trip test on SQLite passes whatever the declared width is. It cannot
distinguish the broken schema from the fixed one. Asserting on the mapped column
TYPE can, on any backend, which is what makes it a usable guard in the default
test run.

``test_postgres_collector.py`` carries the execution-level proof, where the
declared type is enforced.
"""

from __future__ import annotations

import time

from sqlalchemy import BigInteger

from voicegateway.middleware.turn_tracker_middleware import TurnRow, TurnTracker
from voicegateway.models.turn_model import Turn
from voicegateway.repository import turns_repository as turns
from voicegateway.services.storage_service import StorageService

# ~79.7 days of host uptime in milliseconds, the value from the production
# traceback. Over int32 (2147483647), which is only 24.9 days.
_OVERFLOWING_MS = 6886684364

_MS_COLUMNS = (
    "caller_speak_start_ms",
    "caller_speak_end_ms",
    "agent_speak_start_ms",
    "agent_speak_end_ms",
    "response_speed_ms",
)


def test_every_ms_column_is_64_bit() -> None:
    """The guard that works on SQLite, where the bug is invisible at runtime.

    A round-trip cannot fail here, so the declaration is what gets checked.
    """
    for name in _MS_COLUMNS:
        column = Turn.__table__.columns[name]
        assert isinstance(column.type, BigInteger), (
            f"turns.{name} is {column.type!r}, not BigInteger. On Postgres that "
            "is int32 and every insert of a millisecond timestamp fails with "
            "'value out of int32 range', answering 503 from /v1/ingest/turns"
        )


def test_it_matches_what_clickhouse_already_declared() -> None:
    """The two stores disagreed about the same field; they must not again.

    ``clickhouse/migrations/0003_turns.sql`` declared all five Int64 from the
    start. Reading it here keeps the SQL side from drifting back on its own.
    """
    from pathlib import Path

    sql = (
        Path(__file__).resolve().parents[2]
        / "clickhouse"
        / "migrations"
        / "0003_turns.sql"
    ).read_text()
    for name in _MS_COLUMNS:
        assert f"{name}" in sql, f"{name} missing from the ClickHouse DDL"
        line = next(ln for ln in sql.splitlines() if ln.strip().startswith(name))
        assert "Int64" in line, (
            f"ClickHouse declares {name} as {line.strip()!r}, which no longer "
            "matches the BigInteger on the ORM model"
        )


async def test_an_out_of_int32_timestamp_round_trips(tmp_path) -> None:
    """Round-trip at the real magnitude.

    This passes on SQLite whether or not the fix is present, which is precisely
    why the type assertion above exists. It is kept because it pins the value
    the repository actually writes, and because on Postgres (see
    test_postgres_collector) the same shape is a genuine gate.
    """
    storage = StorageService(str(tmp_path / "wide.db"))
    row = TurnRow(
        session_id="s-wide",
        turn_index=0,
        caller_speak_start_ms=_OVERFLOWING_MS,
        caller_speak_end_ms=_OVERFLOWING_MS + 500,
        agent_speak_start_ms=_OVERFLOWING_MS + 900,
        agent_speak_end_ms=_OVERFLOWING_MS + 2000,
        response_speed_ms=400,
    )
    await storage.log_turns([row])

    async with storage._conn.session() as db:
        [read_back] = await turns.list_turns_by_session(db, "s-wide")
    assert read_back.caller_speak_start_ms == _OVERFLOWING_MS
    assert read_back.agent_speak_end_ms == _OVERFLOWING_MS + 2000
    await storage.aclose()


def test_the_tracker_stamps_epoch_not_time_since_boot() -> None:
    """A stored absolute monotonic value cannot be compared to anything later.

    ``rooms.py`` merges the turns of every session in a room and sorts them on
    ``caller_speak_start_ms``. With monotonic that is an ordering across two
    unrelated clocks whenever the room saw more than one process, which its own
    comment says is expected ("an agent that reconnected, two agents in one
    room").

    Epoch milliseconds are ~1.76e12; time since boot would have to be 55 years
    to reach that, so the two are unambiguous to tell apart.
    """
    stamped = TurnTracker._now_ms()
    now_epoch_ms = int(time.time() * 1000)
    assert abs(stamped - now_epoch_ms) < 5_000, (
        f"_now_ms() returned {stamped}, which is not epoch milliseconds "
        f"({now_epoch_ms}). A monotonic value here is meaningless once stored"
    )


def test_epoch_milliseconds_do_not_fit_in_int32() -> None:
    """Why the width fix and the clock fix belong in one change.

    Switching to epoch would have broken Postgres on its own if the columns had
    stayed INTEGER, and staying on monotonic would have broken it too, later,
    on any host up for 25 days. One migration covers both.
    """
    assert int(time.time() * 1000) > 2147483647
