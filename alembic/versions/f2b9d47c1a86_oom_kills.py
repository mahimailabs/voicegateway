"""node_samples: kernel OOM kills

Revision ID: f2b9d47c1a86
Revises: e4c7a2b81f93
Create Date: 2026-08-02 19:10:00.000000

The acceptance criterion reads "no crashes, OOM events, port exhaustion,
file-descriptor exhaustion, or unplanned restarts". Only file descriptors were
gated. Restarts are computable from ``process_start_time_seconds``, which is
already stored, and a crash presents as a restart, so that half needs no new
column.

OOM does. ``node_vmstat_oom_kill`` was read off a live prom/node-exporter,
where it reads 0 on a healthy box, so this is measured rather than inferred.

It is deliberately a SEPARATE column and a separate gate rather than being
folded into the restart signal. A crash presents as a restart, so the restart
gate covers crashes; but "no restart" does not prove "no OOM", because the
kernel can kill a child or a sibling process without the scraped service ever
restarting. Letting one clean signal vouch for the other would report a
criterion as covered that was never checked.

Cumulative since boot, so the gate reads an INCREASE within a test window
rather than an absolute: a box that was OOM-killed last week is not this run's
finding. BigInteger because it is a counter, and nullable because NULL means
nobody measured, never zero.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2b9d47c1a86"
down_revision: str | Sequence[str] | None = "e4c7a2b81f93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "node_samples", sa.Column("vmstat_oom_kill", sa.BigInteger(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("node_samples", "vmstat_oom_kill")
