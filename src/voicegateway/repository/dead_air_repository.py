"""Async repo for the ``dead_air_events`` table (ORM)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text

from voicegateway.inference.session.context import current_tenant
from voicegateway.middleware.dead_air_detector import DeadAirEvent

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


_INSERT_EVENT = text(
    "INSERT INTO dead_air_events ("
    "session_id, started_at_ms, duration_ms, threshold_used_ms, tenant_id"
    ") VALUES (:session_id, :started_at_ms, :duration_ms, :threshold_used_ms, :tenant_id)"
)


async def create_event(
    session: AsyncSession,
    event: DeadAirEvent,
    *,
    tenant_id: str | None = None,
) -> None:
    """Insert one ``DeadAirEvent``. Commits the session."""
    resolved = tenant_id if tenant_id is not None else current_tenant()
    await session.execute(
        _INSERT_EVENT,
        {
            "session_id": event.session_id,
            "started_at_ms": event.started_at_ms,
            "duration_ms": event.duration_ms,
            "threshold_used_ms": event.threshold_used_ms,
            "tenant_id": resolved,
        },
    )
    await session.commit()


async def list_events_by_session(
    session: AsyncSession, session_id: str
) -> list[DeadAirEvent]:
    """Return all dead-air events for a session, ordered by ``started_at_ms`` ASC."""
    result = await session.execute(
        text(
            "SELECT session_id, started_at_ms, duration_ms, threshold_used_ms "
            "FROM dead_air_events "
            "WHERE session_id = :session_id "
            "ORDER BY started_at_ms ASC"
        ),
        {"session_id": session_id},
    )
    return [DeadAirEvent(*row) for row in result]


async def count_events_by_filter(
    session: AsyncSession,
    session_id: str | None = None,
    started_after_ms: int | None = None,
    started_before_ms: int | None = None,
) -> int:
    """Count dead-air events matching the supplied filter."""
    clauses: list[str] = []
    params: dict[str, object] = {}
    if session_id is not None:
        clauses.append("session_id = :session_id")
        params["session_id"] = session_id
    if started_after_ms is not None:
        clauses.append("started_at_ms >= :started_after_ms")
        params["started_after_ms"] = started_after_ms
    if started_before_ms is not None:
        clauses.append("started_at_ms < :started_before_ms")
        params["started_before_ms"] = started_before_ms

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    result = await session.execute(
        text(f"SELECT COUNT(*) FROM dead_air_events {where}".strip()), params
    )
    row = result.fetchone()
    return int(row[0]) if row is not None else 0


__all__ = [
    "count_events_by_filter",
    "create_event",
    "list_events_by_session",
]
