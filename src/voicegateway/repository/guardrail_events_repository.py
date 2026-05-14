"""Async repo for guardrail audit events (ORM)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import text

from voicegateway.schemas.guardrail_policy_schema import (
    ACTIVE_GUARDRAIL_ACTIONS,
    GUARDRAIL_CATEGORIES,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

GuardrailEventType = Literal["fired", "bypassed"]


@dataclass(frozen=True)
class GuardrailEvent:
    id: int
    event_type: str
    session_id: str
    tenant_id: str | None
    turn_index: int | None
    category: str | None
    action: str | None
    context_excerpt: str | None
    created_at: str
    project: str | None = None


@dataclass(frozen=True)
class GuardrailAggregate:
    category: str
    action: str
    count: int


@dataclass(frozen=True)
class GuardrailTopSession:
    session_id: str
    project: str | None
    tenant_id: str | None
    count: int
    last_event_at: str | None


_SELECT_FIELDS = (
    "ge.id, ge.event_type, ge.session_id, ge.tenant_id, ge.turn_index, "
    "ge.category, ge.action, ge.context_excerpt, ge.created_at, s.project"
)


def _row_to_event(row: Sequence[Any]) -> GuardrailEvent:
    return GuardrailEvent(
        id=int(row[0]),
        event_type=str(row[1]),
        session_id=str(row[2]),
        tenant_id=None if row[3] is None else str(row[3]),
        turn_index=None if row[4] is None else int(row[4]),
        category=None if row[5] is None else str(row[5]),
        action=None if row[6] is None else str(row[6]),
        context_excerpt=None if row[7] is None else str(row[7]),
        created_at=str(row[8]),
        project=None if len(row) < 10 or row[9] is None else str(row[9]),
    )


def _window_clause(
    *,
    since: str | None = None,
    until: str | None = None,
    project: str | None = None,
    tenant: str | None = None,
    category: str | None = None,
    action: str | None = None,
    event_type: str | None = None,
) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if since:
        clauses.append("ge.created_at >= :since")
        params["since"] = since
    if until:
        clauses.append("ge.created_at < :until")
        params["until"] = until
    if project:
        clauses.append("s.project = :project")
        params["project"] = project
    if tenant is not None:
        if tenant == "":
            clauses.append("ge.tenant_id IS NULL")
        else:
            clauses.append("ge.tenant_id = :tenant")
            params["tenant"] = tenant
    if category:
        clauses.append("ge.category = :category")
        params["category"] = category
    if action:
        clauses.append("ge.action = :action")
        params["action"] = action
    if event_type:
        clauses.append("ge.event_type = :event_type")
        params["event_type"] = event_type
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


async def create_event(
    session: AsyncSession,
    *,
    session_id: str,
    event_type: GuardrailEventType,
    tenant_id: str | None = None,
    turn_index: int | None = None,
    category: str | None = None,
    action: str | None = None,
    context_excerpt: str | None = None,
) -> int:
    """Insert one guardrail audit row and return its integer id."""
    if event_type == "fired":
        if category not in GUARDRAIL_CATEGORIES:
            raise ValueError(f"unknown guardrail category: {category}")
        if action not in ACTIVE_GUARDRAIL_ACTIONS:
            raise ValueError(f"unknown guardrail action: {action}")
    elif event_type == "bypassed":
        category = None
        action = None
    else:
        raise ValueError(f"unknown guardrail event_type: {event_type}")

    result = await session.execute(
        text(
            "INSERT INTO guardrail_events "
            "(event_type, session_id, tenant_id, turn_index, category, action, "
            " context_excerpt) "
            "VALUES (:event_type, :session_id, :tenant_id, :turn_index, "
            " :category, :action, :context_excerpt)"
        ),
        {
            "event_type": event_type,
            "session_id": session_id,
            "tenant_id": tenant_id,
            "turn_index": turn_index,
            "category": category,
            "action": action,
            "context_excerpt": context_excerpt,
        },
    )
    return int(result.lastrowid or 0)


async def list_events_by_session(
    session: AsyncSession, session_id: str
) -> list[GuardrailEvent]:
    result = await session.execute(
        text(
            f"""SELECT {_SELECT_FIELDS}
                FROM guardrail_events ge
                LEFT JOIN sessions s ON s.id = ge.session_id
                WHERE ge.session_id = :session_id
                ORDER BY ge.created_at ASC, ge.id ASC"""
        ),
        {"session_id": session_id},
    )
    return [_row_to_event(row) for row in result]


async def list_events(
    session: AsyncSession,
    *,
    since: str | None = None,
    until: str | None = None,
    project: str | None = None,
    tenant: str | None = None,
    category: str | None = None,
    action: str | None = None,
    event_type: str | None = None,
    limit: int = 100,
) -> list[GuardrailEvent]:
    where, params = _window_clause(
        since=since,
        until=until,
        project=project,
        tenant=tenant,
        category=category,
        action=action,
        event_type=event_type,
    )
    params["limit"] = max(1, min(limit, 1000))
    result = await session.execute(
        text(
            f"""SELECT {_SELECT_FIELDS}
                FROM guardrail_events ge
                LEFT JOIN sessions s ON s.id = ge.session_id
                {where}
                ORDER BY ge.created_at DESC, ge.id DESC
                LIMIT :limit"""
        ),
        params,
    )
    return [_row_to_event(row) for row in result]


async def aggregate_counts(
    session: AsyncSession,
    *,
    since: str | None = None,
    until: str | None = None,
    project: str | None = None,
    tenant: str | None = None,
) -> list[GuardrailAggregate]:
    where, params = _window_clause(
        since=since,
        until=until,
        project=project,
        tenant=tenant,
        event_type="fired",
    )
    result = await session.execute(
        text(
            f"""SELECT ge.category, ge.action, COUNT(*) AS count
                FROM guardrail_events ge
                LEFT JOIN sessions s ON s.id = ge.session_id
                {where}
                GROUP BY ge.category, ge.action
                ORDER BY count DESC, ge.category ASC, ge.action ASC"""
        ),
        params,
    )
    return [
        GuardrailAggregate(category=str(row[0]), action=str(row[1]), count=int(row[2]))
        for row in result
    ]


async def top_sessions_by_category(
    session: AsyncSession,
    *,
    category: str,
    since: str | None = None,
    until: str | None = None,
    project: str | None = None,
    tenant: str | None = None,
    limit: int = 10,
) -> list[GuardrailTopSession]:
    where, params = _window_clause(
        since=since,
        until=until,
        project=project,
        tenant=tenant,
        category=category,
        event_type="fired",
    )
    params["limit"] = max(1, min(limit, 100))
    result = await session.execute(
        text(
            f"""SELECT ge.session_id, s.project, ge.tenant_id,
                       COUNT(*) AS count, MAX(ge.created_at) AS last_event_at
                FROM guardrail_events ge
                LEFT JOIN sessions s ON s.id = ge.session_id
                {where}
                GROUP BY ge.session_id, s.project, ge.tenant_id
                ORDER BY count DESC, last_event_at DESC
                LIMIT :limit"""
        ),
        params,
    )
    return [
        GuardrailTopSession(
            session_id=str(row[0]),
            project=None if row[1] is None else str(row[1]),
            tenant_id=None if row[2] is None else str(row[2]),
            count=int(row[3]),
            last_event_at=None if row[4] is None else str(row[4]),
        )
        for row in result
    ]


__all__ = [
    "GuardrailAggregate",
    "GuardrailEvent",
    "GuardrailTopSession",
    "aggregate_counts",
    "create_event",
    "list_events",
    "list_events_by_session",
    "top_sessions_by_category",
]
