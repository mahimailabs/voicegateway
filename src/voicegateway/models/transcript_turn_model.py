"""ORM model for the ``transcript_turns`` table."""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import Index
from sqlalchemy import text as sa_text
from sqlmodel import Field, SQLModel


class TranscriptTurn(SQLModel, table=True):
    """One spoken turn's transcript within a voice session (user or agent).

    Captured best-effort from the framework's conversation history when a call
    ends. ``seq`` orders the turns within a session; ``role`` is ``user`` or
    ``agent``.
    """

    __tablename__: ClassVar[str] = "transcript_turns"
    __table_args__ = (Index("idx_transcript_turns_session_id", "session_id"),)

    id: int | None = Field(default=None, primary_key=True)
    session_id: str
    seq: int
    role: str  # 'user' | 'agent'
    text: str
    created_at: str = Field(
        sa_column_kwargs={"server_default": sa_text("CURRENT_TIMESTAMP")}
    )
    tenant_id: str | None = None
