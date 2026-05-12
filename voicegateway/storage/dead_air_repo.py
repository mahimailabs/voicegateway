"""Async repo for the ``dead_air_events`` table.

Implements REQ-VG-METRICS-004 (dead-air events). Mirrors the
flat-function-module pattern of ``turns_repo.py``: each function takes
an ``aiosqlite.Connection`` and the caller owns the connection
lifecycle.

The default DeadAirDetector event-callback shape from T03 is
``async def on_event(event: DeadAirEvent) -> None``. ``create_event``
below matches that signature, so T08 can wire detector instances
with ``on_event=functools.partial(dead_air_repo.create_event, db)``.

Schema reference: ``voicegateway/storage/migrations/0003_turns_and_deadair.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from voicegateway.inference._session_context import current_tenant
from voicegateway.middleware.dead_air_detector import DeadAirEvent

if TYPE_CHECKING:
    import aiosqlite


_INSERT_EVENT = (
    "INSERT INTO dead_air_events ("
    "session_id, started_at_ms, duration_ms, threshold_used_ms, tenant_id"
    ") VALUES (?, ?, ?, ?, ?)"
)


def _event_to_params(event: DeadAirEvent, tenant_id: str | None) -> tuple[object, ...]:
    return (
        event.session_id,
        event.started_at_ms,
        event.duration_ms,
        event.threshold_used_ms,
        tenant_id,
    )


async def create_event(
    db: aiosqlite.Connection,
    event: DeadAirEvent,
    *,
    tenant_id: str | None = None,
) -> None:
    """Insert one ``DeadAirEvent``. Commits the connection.

    ``tenant_id`` defaults to ``current_tenant()`` (the v0.4.0
    ContextVar). The dead-air watcher runs inside the session's
    asyncio context, so the ContextVar carries the session's tenant
    without any explicit wiring.

    Signature matches :data:`voicegateway.middleware.dead_air_detector.EventCallback`
    via a partial application that drops the tenant kwarg.
    """
    resolved = tenant_id if tenant_id is not None else current_tenant()
    await db.execute(_INSERT_EVENT, _event_to_params(event, resolved))
    await db.commit()


async def list_events_by_session(
    db: aiosqlite.Connection, session_id: str
) -> list[DeadAirEvent]:
    """Return all dead-air events for a session, ordered by ``started_at_ms`` ASC."""
    cursor = await db.execute(
        "SELECT session_id, started_at_ms, duration_ms, threshold_used_ms "
        "FROM dead_air_events "
        "WHERE session_id = ? "
        "ORDER BY started_at_ms ASC",
        (session_id,),
    )
    return [DeadAirEvent(*row) async for row in cursor]


async def count_events_by_filter(
    db: aiosqlite.Connection,
    session_id: str | None = None,
    started_after_ms: int | None = None,
    started_before_ms: int | None = None,
) -> int:
    """Count dead-air events matching the supplied filter.

    All filters are optional and combined with AND. ``started_after_ms``
    is inclusive; ``started_before_ms`` is exclusive — matches the
    half-open interval convention used elsewhere in the storage layer.
    Passing no filters counts all rows in the table.
    """
    clauses: list[str] = []
    params: list[object] = []
    if session_id is not None:
        clauses.append("session_id = ?")
        params.append(session_id)
    if started_after_ms is not None:
        clauses.append("started_at_ms >= ?")
        params.append(started_after_ms)
    if started_before_ms is not None:
        clauses.append("started_at_ms < ?")
        params.append(started_before_ms)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    cursor = await db.execute(
        f"SELECT COUNT(*) FROM dead_air_events {where}".strip(),
        tuple(params),
    )
    row = await cursor.fetchone()
    return int(row[0]) if row is not None else 0


__all__ = [
    "count_events_by_filter",
    "create_event",
    "list_events_by_session",
]
