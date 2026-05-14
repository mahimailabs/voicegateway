"""ORM model for the ``dead_air_events`` table (0003)."""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import Index, text
from sqlmodel import Field, SQLModel


class DeadAirEvent(SQLModel, table=True):
    """A detected silence event during a voice session."""

    __tablename__: ClassVar[str] = "dead_air_events"
    __table_args__ = (Index("idx_dead_air_session_id", "session_id"),)

    id: int | None = Field(default=None, primary_key=True)
    session_id: str
    started_at_ms: int
    duration_ms: int
    threshold_used_ms: int
    created_at: str = Field(
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )

    # 0005: tenant attribution
    tenant_id: str | None = None
