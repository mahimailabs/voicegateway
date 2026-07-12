"""ORM model for the ``workers`` table (fleet live roster).

One row per fleet worker, keyed by ``(tenant_id, agent_id)``. Workers push a
heartbeat that upserts their row (see
:mod:`voicegateway.repository.workers_repository`); the roster read derives a
liveness ``status`` of ``offline`` when the heartbeat has aged past the TTL.
"""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import BigInteger, UniqueConstraint
from sqlmodel import Field, SQLModel


class Worker(SQLModel, table=True):
    """A fleet worker's last-known presence."""

    __tablename__: ClassVar[str] = "workers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_id", name="uq_workers_tenant_agent"),
    )

    id: int | None = Field(default=None, primary_key=True)
    agent_id: str
    agent_name: str
    project: str = "default"
    tenant_id: str | None = None
    region: str | None
    version: str | None
    host: str | None
    active_sessions: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    status: str = "idle"
    started_at: float | None
    last_seen: float
    memory_rss_bytes: int | None = Field(default=None, sa_type=BigInteger)
    memory_total_bytes: int | None = Field(default=None, sa_type=BigInteger)
