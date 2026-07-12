"""add memory_rss_bytes + memory_total_bytes to workers

Revision ID: f3c8a1d5e9b2
Revises: e1a4c8b6f2d9
Create Date: 2026-07-11 15:00:00.000000

Fleet workers report their process RSS and the effective memory ceiling
(cgroup limit when capped, else system total) on each heartbeat, so the
roster can show per-worker memory headroom. Nullable: existing rows and
heartbeats without a memory sample stay valid.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f3c8a1d5e9b2"
down_revision: str | None = "e1a4c8b6f2d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workers", sa.Column("memory_rss_bytes", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "workers", sa.Column("memory_total_bytes", sa.BigInteger(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("workers", "memory_total_bytes")
    op.drop_column("workers", "memory_rss_bytes")
