"""Async repo for the ``turns`` table (ORM)."""

from __future__ import annotations

import statistics
from typing import TYPE_CHECKING

from sqlalchemy import text

from voicegateway.inference.session.context import current_tenant
from voicegateway.middleware.turn_tracker_middleware import TurnRow

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


_INSERT_TURN = text(
    "INSERT INTO turns ("
    "session_id, turn_index, "
    "caller_speak_start_ms, caller_speak_end_ms, "
    "agent_speak_start_ms, agent_speak_end_ms, "
    "response_speed_ms, tenant_id"
    ") VALUES ("
    ":session_id, :turn_index, "
    ":caller_speak_start_ms, :caller_speak_end_ms, "
    ":agent_speak_start_ms, :agent_speak_end_ms, "
    ":response_speed_ms, :tenant_id"
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
    }


async def create_turn(
    session: AsyncSession,
    turn: TurnRow,
    *,
    tenant_id: str | None = None,
) -> None:
    """Insert one ``TurnRow``. Commits the session."""
    resolved = tenant_id if tenant_id is not None else current_tenant()
    await session.execute(_INSERT_TURN, _turn_to_params(turn, resolved))
    await session.commit()


async def create_turns_bulk(
    session: AsyncSession,
    turns: list[TurnRow],
    *,
    tenant_id: str | None = None,
) -> int:
    """Bulk-insert turns. Returns the number inserted."""
    if not turns:
        return 0
    resolved = tenant_id if tenant_id is not None else current_tenant()
    await session.execute(_INSERT_TURN, [_turn_to_params(t, resolved) for t in turns])
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
            "response_speed_ms "
            "FROM turns WHERE session_id = :session_id "
            "ORDER BY turn_index ASC"
        ),
        {"session_id": session_id},
    )
    return [TurnRow(*row) for row in result]


async def aggregate_response_speed(
    session: AsyncSession,
    session_id: str | None = None,
) -> dict[str, int | None]:
    """Compute p50/p95/p99 of ``response_speed_ms`` over the filtered turns."""
    if session_id is not None:
        result = await session.execute(
            text(
                "SELECT response_speed_ms FROM turns "
                "WHERE session_id = :session_id AND response_speed_ms IS NOT NULL"
            ),
            {"session_id": session_id},
        )
    else:
        result = await session.execute(
            text(
                "SELECT response_speed_ms FROM turns "
                "WHERE response_speed_ms IS NOT NULL"
            )
        )
    values = [int(row[0]) for row in result]
    if not values:
        return {"p50_ms": None, "p95_ms": None, "p99_ms": None}
    if len(values) == 1:
        v = values[0]
        return {"p50_ms": v, "p95_ms": v, "p99_ms": v}
    cuts = statistics.quantiles(values, n=100, method="inclusive")
    # cuts[49] = p50, cuts[94] = p95, cuts[98] = p99
    return {
        "p50_ms": int(cuts[49]),
        "p95_ms": int(cuts[94]),
        "p99_ms": int(cuts[98]),
    }


async def count_overlap_turns(session: AsyncSession, session_id: str) -> int:
    """Return the number of turn pairs that overlap (talk-over events)."""
    result = await session.execute(
        text(
            "SELECT COUNT(*) FROM turns t1 "
            "JOIN turns t0 "
            "  ON t0.session_id = t1.session_id "
            " AND t0.turn_index = t1.turn_index - 1 "
            "WHERE t1.session_id = :session_id "
            "  AND t0.agent_speak_end_ms IS NOT NULL "
            "  AND t1.caller_speak_start_ms < t0.agent_speak_end_ms"
        ),
        {"session_id": session_id},
    )
    row = result.fetchone()
    return int(row[0]) if row is not None else 0


__all__ = [
    "aggregate_response_speed",
    "count_overlap_turns",
    "create_turn",
    "create_turns_bulk",
    "list_turns_by_session",
]
