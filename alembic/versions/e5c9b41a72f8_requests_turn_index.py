"""requests: which turn a call belongs to

Revision ID: e5c9b41a72f8
Revises: d7a2f91c4b03
Create Date: 2026-08-21 18:00:00.000000

Cost could be totalled by model, project, session or agent, but not by TURN.
So "that turn took four seconds" and "that turn cost this much" were two
questions about the same moment that could not be asked together, and the
expensive turn and the slow turn could never be shown to be the same one.

Every input already existed. ``requests`` carried ``session_id`` and ``turns``
carried ``session_id`` + ``turn_index``, but nothing tied a single call to a
single turn, and nothing downstream can reconstruct it: the correlation exists
only at the instant the metric fires, while the tracker still knows which turn
is open.

Nullable, and absent rather than zero. A Pipecat session, or any agent running
with turn capture off, has no turn for a call to belong to, and defaulting to
0 would claim the first turn for every one of them. The index is composite on
(session_id, turn_index) because every question this enables is scoped to one
session first.

Mirrors ``tool_calls.turn_index``, which correlates the same way for the same
reason.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5c9b41a72f8"
down_revision: str | None = "d7a2f91c4b03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "idx_requests_session_turn"


def upgrade() -> None:
    op.add_column(
        "requests",
        sa.Column("turn_index", sa.Integer(), nullable=True),
    )
    op.create_index(_INDEX, "requests", ["session_id", "turn_index"])


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="requests")
    op.drop_column("requests", "turn_index")
