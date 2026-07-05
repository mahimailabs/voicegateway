"""Async repo for the ``workers`` table (fleet live roster).

Workers push a periodic heartbeat that upserts a single row keyed by
``(tenant_id, agent_id)`` via :func:`upsert_heartbeat`. The roster read
(:func:`read_roster`) returns those rows and derives a liveness ``status`` of
``offline`` when the last heartbeat has aged past ``ttl_seconds``, so a worker
that stops sending heartbeats is shown offline without any writer touching its
row.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from voicegateway.models.worker_model import Worker

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class RosterRow:
    """One worker's presence as served to the fleet roster."""

    agent_id: str
    agent_name: str
    project: str
    tenant_id: str | None
    region: str | None
    version: str | None
    host: str | None
    active_sessions: int
    status: str
    started_at: float | None
    last_seen: float


def _fields_from_presence(presence: dict[str, Any]) -> dict[str, Any]:
    """Map a raw presence payload onto the ``workers`` column set."""
    return {
        "agent_id": presence["agent_id"],
        "agent_name": presence["agent_name"],
        "project": presence.get("project", "default"),
        "tenant_id": presence.get("tenant_id"),
        "region": presence.get("region"),
        "version": presence.get("version"),
        "host": presence.get("host"),
        "active_sessions": int(presence.get("active_sessions", 0)),
        "status": presence.get("status", "idle"),
        "started_at": presence.get("started_at"),
        "last_seen": float(presence["ts"]),
    }


async def upsert_heartbeat(db: AsyncSession, presence: dict[str, Any]) -> None:
    """Insert or update one worker row keyed by ``(tenant_id, agent_id)``."""
    fields = _fields_from_presence(presence)
    result = await db.execute(
        select(Worker).where(
            Worker.tenant_id == fields["tenant_id"],
            Worker.agent_id == fields["agent_id"],
        )
    )
    worker = result.scalar_one_or_none()
    if worker is None:
        db.add(Worker(**fields))
    else:
        for key, value in fields.items():
            setattr(worker, key, value)
    await db.commit()


async def read_roster(
    db: AsyncSession,
    *,
    tenant_id: str | None,
    now: float,
    ttl_seconds: float = 45.0,
) -> list[RosterRow]:
    """Return the roster, marking rows ``offline`` when heartbeats are stale."""
    stmt = select(Worker)
    if tenant_id is not None:
        stmt = stmt.where(Worker.tenant_id == tenant_id)
    stmt = stmt.order_by(Worker.last_seen.desc())
    result = await db.execute(stmt)
    rows: list[RosterRow] = []
    for w in result.scalars().all():
        status = "offline" if now - w.last_seen > ttl_seconds else w.status
        rows.append(
            RosterRow(
                agent_id=w.agent_id,
                agent_name=w.agent_name,
                project=w.project,
                tenant_id=w.tenant_id,
                region=w.region,
                version=w.version,
                host=w.host,
                active_sessions=w.active_sessions,
                status=status,
                started_at=w.started_at,
                last_seen=w.last_seen,
            )
        )
    return rows


__all__ = [
    "RosterRow",
    "read_roster",
    "upsert_heartbeat",
]
