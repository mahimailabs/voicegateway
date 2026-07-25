"""add cpu_pct to workers

Revision ID: c9d1e5f7a2b4
Revises: b8f4a1c2d7e9
Create Date: 2026-07-25 17:00:00.000000

The worker heartbeat now samples the process's CPU use as a percent of the
machine's capacity, alongside the existing memory sample, so the roster can show
how much compute a worker is using (and how much is left). Nullable: a worker
that reports no CPU sample (an older agent, or a read that failed) stays valid.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c9d1e5f7a2b4"
down_revision: str | None = "b8f4a1c2d7e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("workers", sa.Column("cpu_pct", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("workers", "cpu_pct")
