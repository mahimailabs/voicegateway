"""add revision to requests, turns and dead_air_events

Revision ID: a3f7d21c9b04
Revises: c5b1e83d0f47
Create Date: 2026-08-18 22:40:00.000000

Which build of the agent's configuration produced a row: the prompt, the model
ids, the voice, the interruption thresholds. Without it, "this got slower last
Tuesday" is answerable only by joining deploy logs kept somewhere else against
timestamps by hand, and that join stops working the moment two versions run at
once, which is every canary and every gradual rollout.

Nullable on every table. A row written before this, or by a caller who declares
no revision, stays valid and every existing read behaves exactly as it did.

OPAQUE BY DESIGN: a content hash, a git sha, a semver string and a deploy id are
all valid values. Nothing here parses it, it is only grouped and filtered by, so
choosing between them stays the operator's business. Indexed on requests where
the per-revision aggregates are read.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a3f7d21c9b04"
down_revision: str | None = "c5b1e83d0f47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("requests", "turns", "dead_air_events")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("revision", sa.String(), nullable=True))
    op.create_index("idx_requests_revision", "requests", ["revision"])


def downgrade() -> None:
    op.drop_index("idx_requests_revision", table_name="requests")
    for table in _TABLES:
        op.drop_column(table, "revision")
