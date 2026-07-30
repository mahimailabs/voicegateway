"""create calls + call_legs, add sessions.room_name / sessions.call_id

Revision ID: e4a7c2f9b1d3
Revises: 06836270c254
Create Date: 2026-07-29 21:00:00.000000

The call becomes first-class. Before this, a call only existed in the schema if
it ran inference (``sessions`` is derived inside ``log_request`` behind
``if record.session_id:``), and the LiveKit room -- the one identifier every
layer shares -- was not a column at all, only a ``metadata LIKE`` scan.

``calls`` is keyed on ``room_sid``; ``room_name`` is nullable and best-effort
because a 503 on INVITE never creates a room, and that failure is the most
important row in a load test. ``call_legs`` holds one row per participant, keyed
``(call_id, participant_sid)``. ``sessions`` gains the two nullable correlation
columns, indexed so the sessions<->calls correlation rate is cheap to measure.

Forward-only: nothing here backfills existing rows, by design.

Chain: this is the first of the end-to-end profiling revisions and it revises the
current (two-parent merge) head ``06836270c254``. Every later revision in that
series must chain linearly off this one -- a second revision sharing this parent
forks the graph, which fails only at ``alembic upgrade head`` on a real
database. ``answer_latency_ms`` / ``answer_latency_source`` are created here
(the schema is written once); the node that computes them needs no migration.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e4a7c2f9b1d3"
down_revision: str | None = "06836270c254"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "calls",
        sa.Column("id", sa.String(), nullable=False),
        # Nullable uniques: NULLs stay distinct on SQLite and PostgreSQL alike,
        # which is why the repository upserts select-then-update rather than with
        # a native ON CONFLICT.
        sa.Column("room_sid", sa.String(), nullable=True),
        sa.Column("room_name", sa.String(), nullable=True),
        sa.Column("origin", sa.String(), nullable=False),
        sa.Column("attempt_id", sa.String(), nullable=True),
        sa.Column("run_id", sa.String(), nullable=True),
        sa.Column("project", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=True),
        sa.Column("agent_id", sa.String(), nullable=True),
        sa.Column("channel", sa.String(), nullable=True),
        sa.Column("direction", sa.String(), nullable=True),
        # BigInteger: epoch milliseconds (~1.8e12) overflow a PostgreSQL INT4,
        # and the derived deltas inherit that range.
        sa.Column("started_at_ms", sa.BigInteger(), nullable=True),
        sa.Column("ended_at_ms", sa.BigInteger(), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("end_reason", sa.String(), nullable=True),
        sa.Column("num_legs", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_probe", sa.Integer(), server_default="0", nullable=False),
        sa.Column("answer_latency_ms", sa.BigInteger(), nullable=True),
        sa.Column("answer_latency_source", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("room_sid", name="uq_calls_room_sid"),
        sa.UniqueConstraint("attempt_id", name="uq_calls_attempt_id"),
    )
    op.create_index("idx_calls_room_name", "calls", ["room_name"], unique=False)
    op.create_index("idx_calls_started_at_ms", "calls", ["started_at_ms"], unique=False)
    op.create_index("idx_calls_project", "calls", ["project"], unique=False)
    op.create_index("idx_calls_run_id", "calls", ["run_id"], unique=False)

    op.create_table(
        "call_legs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("call_id", sa.String(), nullable=False),
        # NOT NULL: half of the unique key, and a NULL there would duplicate the
        # leg on every redelivered webhook.
        sa.Column("participant_sid", sa.String(), nullable=False),
        sa.Column("identity", sa.String(), nullable=True),
        sa.Column("kind", sa.String(), nullable=True),
        sa.Column("region", sa.String(), nullable=True),
        sa.Column("joined_at_ms", sa.BigInteger(), nullable=True),
        sa.Column("left_at_ms", sa.BigInteger(), nullable=True),
        sa.Column("disconnect_reason", sa.String(), nullable=True),
        sa.Column("is_publisher", sa.Integer(), nullable=True),
        sa.Column("attributes_json", sa.Text(), nullable=True),
        sa.Column("first_audio_track_at_ms", sa.BigInteger(), nullable=True),
        sa.Column("audio_track_sid", sa.String(), nullable=True),
        sa.Column("audio_codec", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        # Leads with call_id, so it also serves every "legs of this call" lookup;
        # a separate index on call_id would only cost writes during a burst.
        sa.UniqueConstraint(
            "call_id", "participant_sid", name="uq_call_legs_call_participant"
        ),
    )

    # Direct add_column / create_index (not batch_alter_table): same
    # view-collision rationale as the sessions agent_id migration -- a batch
    # rebuild of sessions would drop the SQLite views built over it.
    op.add_column("sessions", sa.Column("room_name", sa.String(), nullable=True))
    op.add_column("sessions", sa.Column("call_id", sa.String(), nullable=True))
    op.create_index("idx_sessions_room_name", "sessions", ["room_name"], unique=False)
    op.create_index("idx_sessions_call_id", "sessions", ["call_id"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_sessions_call_id", table_name="sessions")
    op.drop_index("idx_sessions_room_name", table_name="sessions")
    op.drop_column("sessions", "call_id")
    op.drop_column("sessions", "room_name")

    op.drop_table("call_legs")

    op.drop_index("idx_calls_run_id", table_name="calls")
    op.drop_index("idx_calls_project", table_name="calls")
    op.drop_index("idx_calls_started_at_ms", table_name="calls")
    op.drop_index("idx_calls_room_name", table_name="calls")
    op.drop_table("calls")
