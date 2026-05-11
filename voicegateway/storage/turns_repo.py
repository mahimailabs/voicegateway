"""Async repo for the ``turns`` table.

Implements REQ-VG-METRICS-002 (response-speed aggregation) and provides
the ``count_overlap_turns`` primitive that REQ-VG-METRICS-003 (talk-over
rate) builds on. The talk-over rate itself is computed in
``finalize_session_metrics`` (T07) as ``count_overlap_turns / total_turns``.

The module is a flat set of async functions, not a class: each function
takes an ``aiosqlite.Connection`` and the caller (the v0.0.5 SQLiteStorage
facade in T07, the TurnTracker flush wiring in T08, or the unit tests in
T20) owns the connection lifecycle. This keeps the repo composable
without forcing a separate object hierarchy.

Schema reference: ``voicegateway/storage/migrations/0003_turns_and_deadair.py``.
"""

from __future__ import annotations

import statistics
from typing import TYPE_CHECKING

from voicegateway.middleware.turn_tracker import TurnRow

if TYPE_CHECKING:
    import aiosqlite


_INSERT_TURN = (
    "INSERT INTO turns ("
    "session_id, turn_index, "
    "caller_speak_start_ms, caller_speak_end_ms, "
    "agent_speak_start_ms, agent_speak_end_ms, "
    "response_speed_ms"
    ") VALUES (?, ?, ?, ?, ?, ?, ?)"
)


def _turn_to_params(turn: TurnRow) -> tuple[object, ...]:
    return (
        turn.session_id,
        turn.turn_index,
        turn.caller_speak_start_ms,
        turn.caller_speak_end_ms,
        turn.agent_speak_start_ms,
        turn.agent_speak_end_ms,
        turn.response_speed_ms,
    )


async def create_turn(db: aiosqlite.Connection, turn: TurnRow) -> None:
    """Insert one ``TurnRow``. Commits the connection."""
    await db.execute(_INSERT_TURN, _turn_to_params(turn))
    await db.commit()


async def create_turns_bulk(db: aiosqlite.Connection, turns: list[TurnRow]) -> int:
    """Bulk-insert turns. Returns the number inserted.

    Intended target of :class:`voicegateway.middleware.turn_tracker.TurnTracker`'s
    ``flush_callback``. Empty input is a no-op (returns 0). Commits the
    connection on success.
    """
    if not turns:
        return 0
    await db.executemany(
        _INSERT_TURN,
        [_turn_to_params(t) for t in turns],
    )
    await db.commit()
    return len(turns)


async def list_turns_by_session(
    db: aiosqlite.Connection, session_id: str
) -> list[TurnRow]:
    """Return all turns for a session, ordered by ``turn_index`` ASC."""
    cursor = await db.execute(
        "SELECT session_id, turn_index, "
        "caller_speak_start_ms, caller_speak_end_ms, "
        "agent_speak_start_ms, agent_speak_end_ms, "
        "response_speed_ms "
        "FROM turns WHERE session_id = ? "
        "ORDER BY turn_index ASC",
        (session_id,),
    )
    return [TurnRow(*row) async for row in cursor]


async def aggregate_response_speed(
    db: aiosqlite.Connection,
    session_id: str | None = None,
) -> dict[str, int | None]:
    """Compute p50/p95/p99 of ``response_speed_ms`` over the filtered turns.

    Returns a dict with keys ``p50_ms``, ``p95_ms``, ``p99_ms`` and integer
    millisecond values, or ``None`` for each when the filter yields no rows
    with a measured response speed (rows where the tracker inferred
    ``caller_speak_end_ms`` carry ``NULL`` for ``response_speed_ms`` per
    the T02 contract).

    The optional ``session_id`` filter scopes the aggregation to one
    session; otherwise the aggregate spans every recorded turn.

    Percentiles use ``statistics.quantiles(n=100, method="inclusive")``
    per the Foundry; ``n=1`` short-circuits to return the single value
    on all three percentile keys to avoid the stdlib's ``n < 2`` error.
    """
    if session_id is not None:
        cursor = await db.execute(
            "SELECT response_speed_ms FROM turns "
            "WHERE session_id = ? AND response_speed_ms IS NOT NULL",
            (session_id,),
        )
    else:
        cursor = await db.execute(
            "SELECT response_speed_ms FROM turns WHERE response_speed_ms IS NOT NULL",
        )
    values = [int(row[0]) async for row in cursor]
    if not values:
        return {"p50_ms": None, "p95_ms": None, "p99_ms": None}
    if len(values) == 1:
        v = values[0]
        return {"p50_ms": v, "p95_ms": v, "p99_ms": v}
    cuts = statistics.quantiles(values, n=100, method="inclusive")
    # statistics.quantiles(n=100) returns 99 cut points;
    # cuts[49] = p50, cuts[94] = p95, cuts[98] = p99
    return {
        "p50_ms": int(cuts[49]),
        "p95_ms": int(cuts[94]),
        "p99_ms": int(cuts[98]),
    }


async def count_overlap_turns(db: aiosqlite.Connection, session_id: str) -> int:
    """Return the number of turn pairs that overlap (talk-over events).

    A turn is an "overlap" when its ``caller_speak_start_ms`` is strictly
    less than the previous turn's ``agent_speak_end_ms`` — meaning the
    caller started speaking before the agent finished speaking. Adjacent
    turns are paired by ``turn_index = previous_turn_index + 1``.

    Turns where the agent never spoke (``agent_speak_end_ms IS NULL``)
    are excluded from the previous-turn side; they cannot host an overlap
    by definition.

    Implements REQ-VG-METRICS-003's data primitive. The talk-over rate
    itself (``count_overlap_turns / total_turns``) is computed in
    ``finalize_session_metrics`` (T07).
    """
    cursor = await db.execute(
        "SELECT COUNT(*) FROM turns t1 "
        "JOIN turns t0 "
        "  ON t0.session_id = t1.session_id "
        " AND t0.turn_index = t1.turn_index - 1 "
        "WHERE t1.session_id = ? "
        "  AND t0.agent_speak_end_ms IS NOT NULL "
        "  AND t1.caller_speak_start_ms < t0.agent_speak_end_ms",
        (session_id,),
    )
    row = await cursor.fetchone()
    return int(row[0]) if row is not None else 0


__all__ = [
    "aggregate_response_speed",
    "count_overlap_turns",
    "create_turn",
    "create_turns_bulk",
    "list_turns_by_session",
]
