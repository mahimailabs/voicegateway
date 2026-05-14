"""Async repo for the sessions table + session-row finalize helpers (ORM)."""

from __future__ import annotations

import dataclasses
import json
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from voicegateway.repository import (
    guardrail_events_repository as guardrail_events,
)
from voicegateway.repository import (
    replay_repository as replay,
)
from voicegateway.repository import (
    turns_repository as turns,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


_SESSION_ORDER_CLAUSES: dict[str, str] = {
    "started_at_desc": "started_at DESC",
    "started_at_asc": "started_at ASC",
    "cost_desc": "total_cost_usd DESC, started_at DESC",
    "cost_asc": "total_cost_usd ASC, started_at DESC",
}


def row_to_session(row: Any) -> dict[str, Any]:
    """Map a fifteen-column session row to the dict shape callers expect."""
    out = {
        "id": row[0],
        "project": row[1],
        "started_at": row[2],
        "ended_at": row[3],
        "modalities": row[4].split(",") if row[4] else [],
        "total_cost_usd": float(row[5] or 0.0),
        "request_count": int(row[6] or 0),
    }
    try:
        tenant_id = row[7]
        out["tenant_id"] = None if tenant_id is None else str(tenant_id)
    except (IndexError, KeyError):
        pass
    try:
        routed_llm = row[8]
        routed_tts = row[9]
        budget_ms = row[10]
        budget_overrun = row[11]
        out["routed_llm"] = None if routed_llm is None else str(routed_llm)
        out["routed_tts"] = None if routed_tts is None else str(routed_tts)
        out["budget_ms"] = None if budget_ms is None else int(budget_ms)
        out["budget_overrun"] = None if budget_overrun is None else bool(budget_overrun)
    except (IndexError, KeyError):
        pass
    try:
        guardrails_active = row[12]
        guardrails_bypassed = row[13]
        policy_snapshot = row[14]
        out["guardrails_active"] = (
            None if guardrails_active is None else bool(guardrails_active)
        )
        out["guardrails_bypassed"] = (
            None if guardrails_bypassed is None else bool(guardrails_bypassed)
        )
        out["guardrail_policy_snapshot"] = (
            json.loads(policy_snapshot) if policy_snapshot else None
        )
    except (IndexError, KeyError, TypeError, ValueError):
        pass
    return out


_BASE_SELECT = (
    "SELECT id, project, started_at, ended_at, modalities, "
    "       total_cost_usd, request_count, tenant_id, "
    "       routed_llm, routed_tts, budget_ms, budget_overrun, "
    "       guardrails_active, guardrails_bypassed, "
    "       guardrail_policy_snapshot_json "
    "FROM sessions"
)


async def list_sessions(
    session: AsyncSession,
    limit: int = 100,
    project: str | None = None,
    order_by: str = "started_at_desc",
    tenant: str | None = None,
) -> list[dict[str, Any]]:
    """Return recent sessions, ordered per ``order_by``."""
    clause = _SESSION_ORDER_CLAUSES.get(order_by)
    if clause is None:
        supported = ", ".join(sorted(_SESSION_ORDER_CLAUSES))
        raise ValueError(f"Unknown order_by {order_by!r}. Supported: {supported}.")
    conditions: list[str] = []
    params: dict[str, Any] = {"limit": limit}
    if project:
        conditions.append("project = :project")
        params["project"] = project
    if tenant is not None:
        if tenant == "":
            conditions.append("tenant_id IS NULL")
        else:
            conditions.append("tenant_id = :tenant")
            params["tenant"] = tenant
    where = f"WHERE {' AND '.join(conditions)} " if conditions else ""
    result = await session.execute(
        text(f"{_BASE_SELECT} {where}ORDER BY {clause} LIMIT :limit"),
        params,
    )
    return [row_to_session(row) for row in result]


async def get_session(session: AsyncSession, session_id: str) -> dict[str, Any] | None:
    """Return one session by id + per-modality / provider / guardrail breakdowns."""
    result = await session.execute(
        text(f"{_BASE_SELECT} WHERE id = :session_id"),
        {"session_id": session_id},
    )
    row = result.fetchone()
    if row is None:
        return None
    out = row_to_session(row)

    mod_result = await session.execute(
        text(
            "SELECT modality, COALESCE(SUM(cost_usd), 0) AS cost, "
            "COUNT(*) AS request_count "
            "FROM requests WHERE session_id = :session_id GROUP BY modality"
        ),
        {"session_id": session_id},
    )
    out["by_modality"] = {
        mod_row[0]: {
            "cost": float(mod_row[1] or 0.0),
            "request_count": int(mod_row[2] or 0),
        }
        for mod_row in mod_result
    }

    prov_result = await session.execute(
        text(
            "SELECT DISTINCT provider FROM requests "
            "WHERE session_id = :session_id ORDER BY provider"
        ),
        {"session_id": session_id},
    )
    out["providers"] = [prov_row[0] for prov_row in prov_result]
    events = await guardrail_events.list_events_by_session(session, session_id)
    out["guardrail_events"] = [dataclasses.asdict(event) for event in events]
    return out


async def finalize_session_metrics(session: AsyncSession, session_id: str) -> None:
    """Recompute and upsert the five aggregate columns on a session row."""
    session_turns = await turns.list_turns_by_session(session, session_id)
    if not session_turns:
        return

    talk_time_ms = 0
    for t in session_turns:
        talk_time_ms += t.caller_speak_end_ms - t.caller_speak_start_ms
        if t.agent_speak_start_ms is not None and t.agent_speak_end_ms is not None:
            talk_time_ms += t.agent_speak_end_ms - t.agent_speak_start_ms
    talk_time_seconds = talk_time_ms / 1000.0

    cost_result = await session.execute(
        text("SELECT total_cost_usd FROM sessions WHERE id = :session_id"),
        {"session_id": session_id},
    )
    cost_row = cost_result.fetchone()
    total_cost = (
        float(cost_row[0]) if cost_row is not None and cost_row[0] is not None else 0.0
    )
    per_minute_cost = (
        total_cost / (talk_time_seconds / 60.0) if talk_time_seconds > 0 else None
    )

    pcts = await turns.aggregate_response_speed(session, session_id)
    overlap_count = await turns.count_overlap_turns(session, session_id)
    total_turns = len(session_turns)
    talk_over_rate = overlap_count / total_turns if total_turns > 0 else None

    await session.execute(
        text(
            "UPDATE sessions "
            "   SET talk_time_seconds = :tts, "
            "       per_minute_cost_usd = :pmc, "
            "       response_speed_p50_ms = :p50, "
            "       response_speed_p95_ms = :p95, "
            "       talk_over_rate = :tor "
            " WHERE id = :session_id"
        ),
        {
            "tts": talk_time_seconds,
            "pmc": per_minute_cost,
            "p50": pcts["p50_ms"],
            "p95": pcts["p95_ms"],
            "tor": talk_over_rate,
            "session_id": session_id,
        },
    )
    await session.commit()


async def finalize_session_replay(session: AsyncSession, session_id: str) -> None:
    """Compute ``replay_size_bytes`` for the session and upsert the column."""
    size_bytes = await replay.aggregate_storage_per_session(session, session_id)
    if size_bytes <= 0:
        return
    await session.execute(
        text(
            "UPDATE sessions SET replay_size_bytes = :size_bytes WHERE id = :session_id"
        ),
        {"size_bytes": size_bytes, "session_id": session_id},
    )
    await session.commit()


__all__ = [
    "finalize_session_metrics",
    "finalize_session_replay",
    "get_session",
    "list_sessions",
    "row_to_session",
]
