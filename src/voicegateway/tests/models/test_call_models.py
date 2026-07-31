"""Shape guards for ``calls`` / ``call_legs`` and the sessions correlation columns.

Three of these encode decisions that are invisible at runtime on SQLite and only
bite on PostgreSQL or in production: epoch-millisecond columns must be
``BigInteger`` (INT4 overflows at ~2.1e9, epoch ms is ~1.8e12), ``room_name``
must stay nullable (a 503 on INVITE never creates a room), and
``participant_sid`` must stay NOT NULL (a NULL in a unique constraint is
distinct from every other NULL, so a redelivered webhook would duplicate the
leg).
"""

from __future__ import annotations

import pytest
from sqlalchemy import BigInteger
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

from voicegateway.models.call_leg_model import CallLeg
from voicegateway.models.call_model import Call
from voicegateway.models.session_model import Session


async def test_call_tables_create() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
    finally:
        await engine.dispose()

    assert Call.__tablename__ == "calls"
    assert CallLeg.__tablename__ == "call_legs"
    assert "calls" in SQLModel.metadata.tables
    assert "call_legs" in SQLModel.metadata.tables


@pytest.mark.parametrize(
    "column",
    ["started_at_ms", "ended_at_ms", "duration_ms", "answer_latency_ms"],
)
def test_call_millisecond_columns_are_bigint(column: str) -> None:
    """Epoch milliseconds overflow a PostgreSQL INT4; SQLite hides it."""
    col = Call.__table__.c[column]
    assert isinstance(col.type, BigInteger), (
        f"calls.{column} is {col.type!r}; epoch-millisecond values (~1.8e12) and "
        "spans derived from them must be BigInteger or PostgreSQL rejects them"
    )


@pytest.mark.parametrize(
    "column", ["joined_at_ms", "left_at_ms", "first_audio_track_at_ms"]
)
def test_call_leg_millisecond_columns_are_bigint(column: str) -> None:
    col = CallLeg.__table__.c[column]
    assert isinstance(col.type, BigInteger), (
        f"call_legs.{column} is {col.type!r}; epoch milliseconds must be BigInteger"
    )


def test_room_name_is_nullable_and_room_sid_is_the_unique_key() -> None:
    columns = Call.__table__.c
    assert columns["room_name"].nullable, (
        "room_name must stay nullable: a 503 on INVITE never creates a room, and "
        "that failure is the most important row in a load test"
    )
    assert columns["room_sid"].nullable, "room_sid is nullable for the same reason"
    unique = {
        tuple(sorted(col.name for col in constraint.columns))
        for constraint in Call.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("room_sid",) in unique
    assert ("attempt_id",) in unique
    # room_name must NOT be unique: a deployment pinning one fixed room name
    # would otherwise collapse two concurrent calls into one row.
    assert ("room_name",) not in unique


def test_call_leg_participant_sid_is_not_null_and_part_of_the_unique_key() -> None:
    columns = CallLeg.__table__.c
    assert not columns["participant_sid"].nullable
    unique = {
        tuple(sorted(col.name for col in constraint.columns))
        for constraint in CallLeg.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("call_id", "participant_sid") in unique


def test_no_loss_jitter_or_mos_columns_are_reserved() -> None:
    """Per-call RTP loss / jitter / MOS is not observable server-side. An empty
    column invites a UI that renders 0.0 as "no loss"."""
    reserved = {"loss_pct", "jitter_ms", "mos", "packet_loss", "dtmf"}
    for model in (Call, CallLeg):
        names = set(model.__table__.c.keys())
        assert not (names & reserved), (
            f"{model.__tablename__} reserves unobservable columns: "
            f"{sorted(names & reserved)}"
        )


def test_sessions_gains_nullable_correlation_columns() -> None:
    columns = Session.__table__.c
    for name in ("room_name", "call_id"):
        assert name in columns, f"sessions.{name} missing (calls correlation)"
        assert columns[name].nullable, (
            f"sessions.{name} must be nullable: web and Pipecat sessions have no "
            "LiveKit room, and there is no backfill by design"
        )
    indexed = {
        tuple(col.name for col in index.columns)
        for index in Session.__table__.indexes
    }
    # Indexed so the sessions<->calls correlation rate is cheap to measure.
    assert ("room_name",) in indexed
    assert ("call_id",) in indexed
