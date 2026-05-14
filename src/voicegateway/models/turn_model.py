"""ORM model for the ``turns`` table (0003)."""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import Index, text
from sqlmodel import Field, SQLModel


class Turn(SQLModel, table=True):
    """One caller-then-agent speech turn within a voice session."""

    __tablename__: ClassVar[str] = "turns"
    __table_args__ = (
        Index("idx_turns_session_id", "session_id"),
        Index("idx_turns_response_speed", "response_speed_ms"),
    )

    id: int | None = Field(default=None, primary_key=True)
    session_id: str
    turn_index: int
    caller_speak_start_ms: int
    caller_speak_end_ms: int
    agent_speak_start_ms: int | None = None
    agent_speak_end_ms: int | None = None
    response_speed_ms: int | None = None
    created_at: str = Field(
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )

    # 0005: tenant attribution
    tenant_id: str | None = None
