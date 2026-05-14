"""ORM models for the four replay-event tables (0004)."""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import Index, text
from sqlmodel import Field, SQLModel


class ReplaySttEvent(SQLModel, table=True):
    """One STT event captured for session replay."""

    __tablename__: ClassVar[str] = "replay_stt_events"
    __table_args__ = (Index("idx_replay_stt_session_t", "session_id", "t_ms"),)

    id: int | None = Field(default=None, primary_key=True)
    session_id: str
    t_ms: int
    payload: str
    provider: str = Field(default="", sa_column_kwargs={"server_default": ""})
    cost_usd: float | None = None
    created_at: str = Field(
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )
    tenant_id: str | None = None


class ReplayLlmToken(SQLModel, table=True):
    """One LLM token chunk captured for session replay."""

    __tablename__: ClassVar[str] = "replay_llm_tokens"
    __table_args__ = (Index("idx_replay_llm_session_t", "session_id", "t_ms"),)

    id: int | None = Field(default=None, primary_key=True)
    session_id: str
    t_ms: int
    payload: str
    provider: str = Field(default="", sa_column_kwargs={"server_default": ""})
    cost_usd: float | None = None
    created_at: str = Field(
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )
    tenant_id: str | None = None


class ReplayTtsFrame(SQLModel, table=True):
    """One TTS audio frame captured for session replay."""

    __tablename__: ClassVar[str] = "replay_tts_frames"
    __table_args__ = (Index("idx_replay_tts_session_t", "session_id", "t_ms"),)

    id: int | None = Field(default=None, primary_key=True)
    session_id: str
    t_ms: int
    payload: str
    provider: str = Field(default="", sa_column_kwargs={"server_default": ""})
    cost_usd: float | None = None
    created_at: str = Field(
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )
    tenant_id: str | None = None


class ReplayStateSnapshot(SQLModel, table=True):
    """A point-in-time state snapshot captured during a session."""

    __tablename__: ClassVar[str] = "replay_state_snapshots"
    __table_args__ = (Index("idx_replay_state_session_t", "session_id", "t_ms"),)

    id: int | None = Field(default=None, primary_key=True)
    session_id: str
    t_ms: int
    payload: str
    created_at: str = Field(
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )
    tenant_id: str | None = None
