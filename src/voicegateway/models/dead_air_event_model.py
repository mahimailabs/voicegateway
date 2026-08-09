"""ORM model for the ``dead_air_events`` table (0003)."""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import BigInteger, Column, Index, text
from sqlmodel import Field, SQLModel


class DeadAirEvent(SQLModel, table=True):
    """A detected silence event during a voice session.

    The ``_ms`` columns are BigInteger for the same reason as ``turns``:
    ``started_at_ms`` is a millisecond timestamp, which exceeds int32 and makes
    every insert fail on Postgres with "value out of int32 range". SQLite hides
    it, so the default test run cannot see it.

    ``duration_ms`` and ``threshold_used_ms`` are deltas that fit, and are
    widened with the rest so the row is uniform.
    """

    __tablename__: ClassVar[str] = "dead_air_events"
    __table_args__ = (Index("idx_dead_air_session_id", "session_id"),)

    id: int | None = Field(default=None, primary_key=True)
    session_id: str
    started_at_ms: int = Field(sa_column=Column(BigInteger(), nullable=False))
    duration_ms: int = Field(sa_column=Column(BigInteger(), nullable=False))
    threshold_used_ms: int = Field(sa_column=Column(BigInteger(), nullable=False))
    created_at: str = Field(
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )

    # 0005: tenant attribution
    tenant_id: str | None = None
