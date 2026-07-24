"""create transcript_turns table

Revision ID: b7d2f4a9c1e3
Revises: f3c8a1d5e9b2
Create Date: 2026-07-24 18:00:00.000000

Per-turn call transcripts, captured best-effort from the framework's
conversation history when a call ends (default on; disable with attach(
transcript=False) or the VOICEGW_TRANSCRIPTS kill-switch). One row per user or
agent turn; ``seq`` orders them within a session. Ages out with the session via
the retention worker (transcript_turns is a session child table).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel  # noqa: F401 - migrations may reference SQLModel types

from alembic import op

revision: str = "b7d2f4a9c1e3"
down_revision: str | None = "f3c8a1d5e9b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "transcript_turns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("text", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "created_at",
            sqlmodel.sql.sqltypes.AutoString(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("tenant_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("transcript_turns", schema=None) as batch_op:
        batch_op.create_index(
            "idx_transcript_turns_session_id", ["session_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("transcript_turns", schema=None) as batch_op:
        batch_op.drop_index("idx_transcript_turns_session_id")
    op.drop_table("transcript_turns")
