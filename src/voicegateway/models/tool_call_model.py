"""ORM model for the ``tool_calls`` table.

On agents that call tools, the tool is usually the largest term in a slow turn
and it was the one term the views could not show. ``llm_ttft_ms`` and
``tts_ttfb_ms`` are both small and both visible; the multi-second external call
sitting between them was neither.

NO ARGUMENTS AND NO RESULTS ARE STORED, and there is deliberately no column for
either. A tool's payload is the operator's data, and whatever their tools handle
is not ours to keep by default. A name and a duration are a TIMING MEASUREMENT;
the payload is a DISCLOSURE. Keeping them apart is exactly what lets this
default on, the same argument by which ``snapshots`` defaults off.
"""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import BigInteger, Column, Index, text
from sqlmodel import Field, SQLModel


class ToolCall(SQLModel, table=True):
    """One tool call: what ran, when, for how long, and how it ended."""

    __tablename__: ClassVar[str] = "tool_calls"
    __table_args__ = (
        Index("idx_tool_calls_session_id", "session_id"),
        Index("idx_tool_calls_tool_name", "tool_name"),
    )

    id: int | None = Field(default=None, primary_key=True)
    session_id: str
    call_id: str
    tool_name: str
    # BigInteger for the same reason as turns and dead_air_events: a millisecond
    # epoch exceeds int32 and every insert fails on Postgres with "value out of
    # int32 range", while SQLite hides it so the default test run cannot see it.
    started_at_ms: int = Field(sa_column=Column(BigInteger(), nullable=False))
    # Null while a call is in flight and for one that never reported an end.
    # Absent is not zero: a zero duration would read as an instant tool.
    duration_ms: int | None = Field(
        default=None, sa_column=Column(BigInteger(), nullable=True)
    )
    # "completed" | "failed" | "cancelled". Null means no terminal event was
    # seen, which is a different fact from any of the three.
    outcome: str | None = None
    # The turn this call belongs to, so tool time can be attributed to the wait
    # a caller actually experienced rather than floating free in the session.
    turn_index: int | None = None
    created_at: str = Field(
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )
    tenant_id: str | None = None
    # Agent configuration revision. Carried because #245's acceptance says the
    # revision appears on every row type, and this row type was added after it.
    revision: str | None = None
