"""Async repo for the sessions table + session-row finalize helpers."""

from __future__ import annotations

import dataclasses
import json
from typing import TYPE_CHECKING, Any

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
    import aiosqlite


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


async def list_sessions(
    db: aiosqlite.Connection,
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
    params: list[Any] = []
    if project:
        conditions.append("project = ?")
        params.append(project)
    if tenant is not None:
        if tenant == "":
            conditions.append("tenant_id IS NULL")
        else:
            conditions.append("tenant_id = ?")
            params.append(tenant)
    where = f"WHERE {' AND '.join(conditions)} " if conditions else ""
    params.append(limit)
    cursor = await db.execute(
        f"""SELECT id, project, started_at, ended_at, modalities,
                  total_cost_usd, request_count, tenant_id,
                  routed_llm, routed_tts, budget_ms, budget_overrun,
                  guardrails_active, guardrails_bypassed,
                  guardrail_policy_snapshot_json
           FROM sessions
           {where}ORDER BY {clause}
           LIMIT ?""",
        tuple(params),
    )
    return [row_to_session(row) async for row in cursor]


async def get_session(
    db: aiosqlite.Connection, session_id: str
) -> dict[str, Any] | None:
    """Return one session by id plus per-modality / provider / guardrail-event breakdowns."""
    cursor = await db.execute(
        """SELECT id, project, started_at, ended_at, modalities,
                  total_cost_usd, request_count, tenant_id,
                  routed_llm, routed_tts, budget_ms, budget_overrun,
                  guardrails_active, guardrails_bypassed,
                  guardrail_policy_snapshot_json
           FROM sessions
           WHERE id = ?""",
        (session_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    session = row_to_session(row)

    mod_cursor = await db.execute(
        """SELECT modality,
                  COALESCE(SUM(cost_usd), 0) AS cost,
                  COUNT(*) AS request_count
           FROM requests
           WHERE session_id = ?
           GROUP BY modality""",
        (session_id,),
    )
    session["by_modality"] = {
        mod_row[0]: {
            "cost": float(mod_row[1] or 0.0),
            "request_count": int(mod_row[2] or 0),
        }
        async for mod_row in mod_cursor
    }

    prov_cursor = await db.execute(
        """SELECT DISTINCT provider
           FROM requests
           WHERE session_id = ?
           ORDER BY provider""",
        (session_id,),
    )
    session["providers"] = [prov_row[0] async for prov_row in prov_cursor]
    events = await guardrail_events.list_events_by_session(db, session_id)
    session["guardrail_events"] = [dataclasses.asdict(event) for event in events]
    return session


async def finalize_session_metrics(db: aiosqlite.Connection, session_id: str) -> None:
    """Recompute and upsert the five aggregate columns on a session row."""
    session_turns = await turns.list_turns_by_session(db, session_id)
    if not session_turns:
        return

    talk_time_ms = 0
    for t in session_turns:
        talk_time_ms += t.caller_speak_end_ms - t.caller_speak_start_ms
        if t.agent_speak_start_ms is not None and t.agent_speak_end_ms is not None:
            talk_time_ms += t.agent_speak_end_ms - t.agent_speak_start_ms
    talk_time_seconds = talk_time_ms / 1000.0

    cost_cursor = await db.execute(
        "SELECT total_cost_usd FROM sessions WHERE id = ?",
        (session_id,),
    )
    cost_row = await cost_cursor.fetchone()
    total_cost = (
        float(cost_row[0]) if cost_row is not None and cost_row[0] is not None else 0.0
    )
    per_minute_cost = (
        total_cost / (talk_time_seconds / 60.0) if talk_time_seconds > 0 else None
    )

    pcts = await turns.aggregate_response_speed(db, session_id)
    overlap_count = await turns.count_overlap_turns(db, session_id)
    total_turns = len(session_turns)
    talk_over_rate = overlap_count / total_turns if total_turns > 0 else None

    await db.execute(
        """UPDATE sessions
              SET talk_time_seconds = ?,
                  per_minute_cost_usd = ?,
                  response_speed_p50_ms = ?,
                  response_speed_p95_ms = ?,
                  talk_over_rate = ?
            WHERE id = ?""",
        (
            talk_time_seconds,
            per_minute_cost,
            pcts["p50_ms"],
            pcts["p95_ms"],
            talk_over_rate,
            session_id,
        ),
    )
    await db.commit()


async def finalize_session_replay(db: aiosqlite.Connection, session_id: str) -> None:
    """Compute ``replay_size_bytes`` for the session and upsert the column."""
    size_bytes = await replay.aggregate_storage_per_session(db, session_id)
    if size_bytes <= 0:
        return
    await db.execute(
        "UPDATE sessions SET replay_size_bytes = ? WHERE id = ?",
        (size_bytes, session_id),
    )
    await db.commit()


__all__ = [
    "finalize_session_metrics",
    "finalize_session_replay",
    "get_session",
    "list_sessions",
    "row_to_session",
]
