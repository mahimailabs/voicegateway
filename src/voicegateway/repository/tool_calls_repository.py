"""Async repo for the ``tool_calls`` table.

Nothing here reads or writes a tool's arguments or results. There is no column
for either and no parameter that could carry one: a name and a duration are a
timing measurement, a payload is a disclosure, and that separation is what lets
this capture default on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from voicegateway.inference.session.context import current_tenant
from voicegateway.models.tool_call_model import ToolCall

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


_INSERT = text(
    "INSERT INTO tool_calls ("
    "session_id, call_id, tool_name, started_at_ms, duration_ms, outcome, "
    "turn_index, tenant_id, revision"
    ") VALUES ("
    ":session_id, :call_id, :tool_name, :started_at_ms, :duration_ms, :outcome, "
    ":turn_index, :tenant_id, :revision"
    ")"
)


async def create_tool_calls(
    session: AsyncSession,
    rows: list[ToolCall],
    *,
    tenant_id: str | None = None,
) -> int:
    """Insert tool-call rows. Commits the session. Returns how many landed."""
    if not rows:
        return 0
    resolved = tenant_id if tenant_id is not None else current_tenant()
    await session.execute(
        _INSERT,
        [
            {
                "session_id": r.session_id,
                "call_id": r.call_id,
                "tool_name": r.tool_name,
                "started_at_ms": r.started_at_ms,
                "duration_ms": r.duration_ms,
                "outcome": r.outcome,
                "turn_index": r.turn_index,
                "tenant_id": r.tenant_id if r.tenant_id is not None else resolved,
                "revision": r.revision,
            }
            for r in rows
        ],
    )
    await session.commit()
    return len(rows)


async def list_by_session(session: AsyncSession, session_id: str) -> list[ToolCall]:
    """All tool calls for one session, oldest first."""
    result = await session.execute(
        text(
            "SELECT session_id, call_id, tool_name, started_at_ms, duration_ms, "
            "outcome, turn_index, tenant_id, revision "
            "FROM tool_calls WHERE session_id = :session_id "
            "ORDER BY started_at_ms ASC"
        ),
        {"session_id": session_id},
    )
    return [
        ToolCall(
            session_id=r[0],
            call_id=r[1],
            tool_name=r[2],
            started_at_ms=r[3],
            duration_ms=r[4],
            outcome=r[5],
            turn_index=r[6],
            tenant_id=r[7],
            revision=r[8],
        )
        for r in result
    ]


async def aggregate_by_tool(
    session: AsyncSession,
    *,
    session_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Per-tool call count, total and average duration, and failure counts.

    Averaged over calls that REPORTED a duration. A call still in flight, or one
    whose end was never seen, contributes to ``calls`` and not to ``avg_ms``:
    counting it as zero would pull the average toward "instant" precisely when a
    tool hung, which is the case somebody is looking for.
    """
    where = ""
    params: dict[str, Any] = {}
    if session_id is not None:
        where = "WHERE session_id = :session_id"
        params["session_id"] = session_id
    result = await session.execute(
        text(
            f"""SELECT tool_name,
                       COUNT(*) AS calls,
                       SUM(CASE WHEN duration_ms IS NOT NULL THEN 1 ELSE 0 END) AS timed,
                       SUM(COALESCE(duration_ms, 0)) AS total_ms,
                       SUM(CASE WHEN outcome = 'failed' THEN 1 ELSE 0 END) AS failed,
                       SUM(CASE WHEN outcome = 'cancelled' THEN 1 ELSE 0 END) AS cancelled
                FROM tool_calls {where}
                GROUP BY tool_name ORDER BY total_ms DESC"""
        ),
        params,
    )
    out: dict[str, dict[str, Any]] = {}
    for name, calls, timed, total_ms, failed, cancelled in result:
        out[name] = {
            "calls": int(calls),
            "total_ms": int(total_ms or 0),
            "avg_ms": (int(total_ms) / int(timed)) if timed else None,
            "failed": int(failed or 0),
            "cancelled": int(cancelled or 0),
        }
    return out


__all__ = ["aggregate_by_tool", "create_tool_calls", "list_by_session"]
