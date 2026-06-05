"""ORM model for the ``agent_observations`` table (Phase 3 fleet rollup)."""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import Index, text
from sqlmodel import Field, SQLModel


class AgentObservation(SQLModel, table=True):
    """One denormalized per-agent rollup over the trailing window.

    The ``agent_id IS NULL`` row is the unattributed bucket. The table is
    refreshed wholesale (DELETE + INSERT) by the agent-observations roll-up
    worker, so callers read it as a fast, pre-aggregated snapshot.
    """

    __tablename__: ClassVar[str] = "agent_observations"
    __table_args__ = (Index("idx_agent_obs_agent_id", "agent_id"),)

    id: int | None = Field(default=None, primary_key=True)
    agent_id: str | None = None
    request_count: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    total_cost_usd: float = Field(default=0.0, sa_column_kwargs={"server_default": "0"})
    error_count: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    p50_ms: int | None = None
    p95_ms: int | None = None
    last_seen: float | None = None
    window_start: str
    window_end: str
    refreshed_at: str = Field(
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )
