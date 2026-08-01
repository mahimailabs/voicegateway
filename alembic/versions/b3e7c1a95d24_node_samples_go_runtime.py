"""add the Go runtime pair to node_samples (return-to-baseline evidence)

Revision ID: b3e7c1a95d24
Revises: a9e2c7b4f1d6
Create Date: 2026-07-31 22:30:00.000000

Two columns so a run can be shown to have given its resources back after
teardown: ``heap_inuse_bytes`` and ``go_goroutines``, both scraped from the
standard ``prometheus/client_golang`` collectors that livekit-server and
livekit-sip register by default.

**RSS is deliberately not here.** Go returns freed heap to the OS lazily, so a
process that has fully drained can hold its resident size for a long time
afterwards. A gate on RSS would report a leak on healthy runs, which trains a
reader to ignore it. ``heap_inuse`` is what the runtime still holds, and a
goroutine count that does not come back down is the shape a real leak takes in
these binaries: a per-call goroutine that never exits.

Both nullable, like every other value column in this table, and NULL means "not
measured" rather than zero. A 0 in ``go_goroutines`` would describe a process
with no goroutines at all, which is not a state a running Go program is in.

``heap_inuse_bytes`` is BigInteger for the same reason the memory columns are:
an INT4 takes neither a large heap nor a large limit, silently on SQLite and as
a 500 on PostgreSQL.

No index. This table already carries two and takes ~57k inserts/day; these
columns are read as part of a window that is already selected by
``(node, source, at_ms)``.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b3e7c1a95d24"
down_revision = "a9e2c7b4f1d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "node_samples",
        sa.Column("heap_inuse_bytes", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "node_samples",
        sa.Column("go_goroutines", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("node_samples", "go_goroutines")
    op.drop_column("node_samples", "heap_inuse_bytes")
