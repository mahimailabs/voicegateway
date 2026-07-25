"""Async repo for the ``agent_probe_results`` cache.

A probe upserts one row per agent (:func:`upsert_probe_result`); the fleet index
reads them back (:func:`read_probe_results`) so the Agents page shows a per-agent
latency graph without re-running a billed probe on every view.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from voicegateway.models.agent_probe_result_model import AgentProbeResult

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def upsert_probe_result(
    db: AsyncSession, agent_id: str, result: dict[str, Any], created_at: float
) -> None:
    """Store ``result`` as this agent's latest cached probe, overwriting any prior.

    Select-then-update (not a native ON CONFLICT) so it behaves the same on
    SQLite and Postgres; a racing first insert is caught and retried as an update.
    """
    payload = json.dumps(result)
    stmt = select(AgentProbeResult).where(
        AgentProbeResult.agent_id == agent_id  # type: ignore[arg-type]
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        db.add(
            AgentProbeResult(
                agent_id=agent_id, result_json=payload, created_at=created_at
            )
        )
    else:
        row.result_json = payload
        row.created_at = created_at
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        row = (await db.execute(stmt)).scalar_one()
        row.result_json = payload
        row.created_at = created_at
        await db.commit()


async def read_probe_results(
    db: AsyncSession, agent_ids: list[str] | None = None
) -> dict[str, dict[str, Any]]:
    """Return ``{agent_id: {"result": <dict>, "created_at": <float>}}``.

    A row whose ``result_json`` will not parse is skipped rather than raising, so
    one bad cache entry never breaks the whole fleet index.
    """
    stmt = select(AgentProbeResult)
    if agent_ids:
        stmt = stmt.where(AgentProbeResult.agent_id.in_(agent_ids))  # type: ignore[attr-defined]
    rows = (await db.execute(stmt)).scalars().all()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        try:
            result = json.loads(r.result_json)
        except (ValueError, TypeError):
            continue
        out[r.agent_id] = {"result": result, "created_at": r.created_at}
    return out
