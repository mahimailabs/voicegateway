"""ORM model for the ``turns`` table (0003)."""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import BigInteger, Column, Index, text
from sqlmodel import Field, SQLModel


class Turn(SQLModel, table=True):
    """One caller-then-agent speech turn within a voice session.

    Every ``_ms`` column is BigInteger, not the plain ``int`` SQLModel maps to
    INTEGER. These hold epoch milliseconds, which is ~1.76e12 and exceeds
    int32's 2147483647 by three orders of magnitude, so INTEGER makes every
    insert fail on Postgres with "value out of int32 range" and the endpoint
    answer 503. SQLite hides it: its dynamic typing stores the value regardless,
    so a SQLite-only test suite stays green while Postgres rejects every row.

    ``response_speed_ms`` is a delta and would fit in int32, but it is widened
    with the rest so the row is uniform and nobody has to remember which of five
    sibling columns is the narrow one.

    ``clickhouse/migrations/0003_turns.sql`` already declared all five Int64.
    The two stores disagreed on the same field; this is the side that was wrong.
    """

    __tablename__: ClassVar[str] = "turns"
    __table_args__ = (
        Index("idx_turns_session_id", "session_id"),
        Index("idx_turns_response_speed", "response_speed_ms"),
    )

    id: int | None = Field(default=None, primary_key=True)
    session_id: str
    turn_index: int
    caller_speak_start_ms: int = Field(sa_column=Column(BigInteger(), nullable=False))
    caller_speak_end_ms: int = Field(sa_column=Column(BigInteger(), nullable=False))
    agent_speak_start_ms: int | None = Field(
        default=None, sa_column=Column(BigInteger(), nullable=True)
    )
    agent_speak_end_ms: int | None = Field(
        default=None, sa_column=Column(BigInteger(), nullable=True)
    )
    response_speed_ms: int | None = Field(
        default=None, sa_column=Column(BigInteger(), nullable=True)
    )
    created_at: str = Field(
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )

    # 0005: tenant attribution
    tenant_id: str | None = None
