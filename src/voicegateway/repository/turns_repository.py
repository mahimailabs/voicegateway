"""Async repo for the ``turns`` table (ORM)."""

from __future__ import annotations

import statistics
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from voicegateway.middleware.turn_tracker_middleware import TurnRow

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# Mirrors MetricsConfig.talk_over_min_overlap_ms. Restated rather than imported
# so the repository layer does not pull in config; the guard test asserts the
# two stay equal.
DEFAULT_TALK_OVER_MIN_OVERLAP_MS = 100


_INSERT_TURN = text(
    "INSERT INTO turns ("
    "session_id, turn_index, "
    "caller_speak_start_ms, caller_speak_end_ms, "
    "agent_speak_start_ms, agent_speak_end_ms, "
    "response_speed_ms, tenant_id, revision"
    ") VALUES ("
    ":session_id, :turn_index, "
    ":caller_speak_start_ms, :caller_speak_end_ms, "
    ":agent_speak_start_ms, :agent_speak_end_ms, "
    ":response_speed_ms, :tenant_id, :revision"
    ")"
)


def _turn_to_params(turn: TurnRow, tenant_id: str | None) -> dict[str, object]:
    return {
        "session_id": turn.session_id,
        "turn_index": turn.turn_index,
        "caller_speak_start_ms": turn.caller_speak_start_ms,
        "caller_speak_end_ms": turn.caller_speak_end_ms,
        "agent_speak_start_ms": turn.agent_speak_start_ms,
        "agent_speak_end_ms": turn.agent_speak_end_ms,
        "response_speed_ms": turn.response_speed_ms,
        "tenant_id": tenant_id,
        "revision": turn.revision,
    }


async def create_turn(
    session: AsyncSession,
    turn: TurnRow,
    *,
    tenant_id: str | None,
) -> None:
    """Insert one ``TurnRow``. Commits the session."""
    await session.execute(_INSERT_TURN, _turn_to_params(turn, tenant_id))
    await session.commit()


async def create_turns_bulk(
    session: AsyncSession,
    turns: list[TurnRow],
    *,
    tenant_id: str | None,
) -> int:
    """Bulk-insert turns. Returns the number inserted."""
    if not turns:
        return 0
    await session.execute(_INSERT_TURN, [_turn_to_params(t, tenant_id) for t in turns])
    await session.commit()
    return len(turns)


async def list_turns_by_session(
    session: AsyncSession, session_id: str
) -> list[TurnRow]:
    """Return all turns for a session, ordered by ``turn_index`` ASC."""
    result = await session.execute(
        text(
            "SELECT session_id, turn_index, "
            "caller_speak_start_ms, caller_speak_end_ms, "
            "agent_speak_start_ms, agent_speak_end_ms, "
            "response_speed_ms, revision "
            "FROM turns WHERE session_id = :session_id "
            "ORDER BY turn_index ASC"
        ),
        {"session_id": session_id},
    )
    return [TurnRow(*row) for row in result]


async def aggregate_response_speed(
    session: AsyncSession,
    session_id: str | None = None,
    *,
    since_ms: int | None = None,
    until_ms: int | None = None,
    project: str | None = None,
    tenant: str | None = None,
) -> dict[str, int | None]:
    """p50/p95/p99 of ``response_speed_ms`` over the filtered turns, and the count.

    A PERCENTILE OVER TURNS, which is not the same statistic as the mean of
    per-session percentiles that ``GET /api/metrics`` reports. On two sessions
    with turn latencies [100,110,120,130,900] and [200,210,220] this returns 662
    where that returns 482. Both are defensible numbers and only one of them is
    a p95, so they must not be read into the same field: a baseline pinned from
    one and checked against the other would compare two different mathematics
    under one name.

    Windowed on ``caller_speak_start_ms``, the turn's own epoch instant, rather
    than on ``created_at``. The latter is when the row was WRITTEN, which lags by
    the buffer flush and would move a turn between windows depending on when the
    agent got round to sending it.

    ``samples`` travels with the values because a percentile over three turns and
    one over two hundred are different claims, and a caller reading only the
    number cannot tell them apart.
    """
    where = ["response_speed_ms IS NOT NULL"]
    params: dict[str, Any] = {}
    if session_id is not None:
        where.append("t.session_id = :session_id")
        params["session_id"] = session_id
    if since_ms is not None:
        where.append("t.caller_speak_start_ms >= :since_ms")
        params["since_ms"] = since_ms
    if until_ms is not None:
        where.append("t.caller_speak_start_ms < :until_ms")
        params["until_ms"] = until_ms
    if tenant is not None:
        if tenant == "":
            where.append("t.tenant_id IS NULL")
        else:
            where.append("t.tenant_id = :tenant")
            params["tenant"] = tenant
    # Project lives on sessions, not on turns, so it needs the join. Only taken
    # when asked for: an unconditional join would make every unfiltered read pay
    # for a column it does not use.
    join = ""
    if project is not None:
        join = " JOIN sessions s ON s.id = t.session_id"
        where.append("s.project = :project")
        params["project"] = project

    result = await session.execute(
        text(
            f"SELECT t.response_speed_ms FROM turns t{join} WHERE {' AND '.join(where)}"
        ),
        params,
    )
    values = [int(row[0]) for row in result]
    if not values:
        return {"p50_ms": None, "p95_ms": None, "p99_ms": None, "samples": 0}
    if len(values) == 1:
        v = values[0]
        return {"p50_ms": v, "p95_ms": v, "p99_ms": v, "samples": 1}
    cuts = statistics.quantiles(values, n=100, method="inclusive")
    # cuts[49] = p50, cuts[94] = p95, cuts[98] = p99
    return {
        "p50_ms": int(cuts[49]),
        "p95_ms": int(cuts[94]),
        "p99_ms": int(cuts[98]),
        "samples": len(values),
    }


async def count_overlap_turns(
    session: AsyncSession,
    session_id: str,
    *,
    min_overlap_ms: int = DEFAULT_TALK_OVER_MIN_OVERLAP_MS,
) -> int:
    """Return the number of turn pairs that overlap (talk-over events).

    ``min_overlap_ms`` is how much the caller must cut into the agent's speech
    before it counts. Any overlap at all used to count, which made a caller
    starting a few milliseconds early indistinguishable from one talking over
    the agent, and is what ``metrics.talk_over_min_overlap_ms`` was declared to
    control before anything read it.
    """
    result = await session.execute(
        text(
            "SELECT COUNT(*) FROM turns t1 "
            "JOIN turns t0 "
            "  ON t0.session_id = t1.session_id "
            " AND t0.turn_index = t1.turn_index - 1 "
            "WHERE t1.session_id = :session_id "
            "  AND t0.agent_speak_end_ms IS NOT NULL "
            "  AND (t0.agent_speak_end_ms - t1.caller_speak_start_ms) "
            "      >= :min_overlap_ms"
        ),
        {"session_id": session_id, "min_overlap_ms": max(1, min_overlap_ms)},
    )
    row = result.fetchone()
    return int(row[0]) if row is not None else 0


__all__ = [
    "DEFAULT_TALK_OVER_MIN_OVERLAP_MS",
    "aggregate_response_speed",
    "count_overlap_turns",
    "create_turn",
    "create_turns_bulk",
    "list_turns_by_session",
]
