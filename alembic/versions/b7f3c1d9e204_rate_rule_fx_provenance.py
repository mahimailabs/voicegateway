"""rate-card FX provenance

Five nullable columns recording where a converted price came from. A rule set
from a foreign-currency figure is otherwise indistinguishable from a
hand-typed one the moment it is written, which makes its staleness invisible
rather than absent: nobody can tell whether $0.03140911 came from Rs 3.00 at
95.51 in September or from a guess.

All nullable and all additive. Every existing rule keeps NULL here, which is
the honest reading: those were entered directly in USD and no rate was
involved.

Revision ID: b7f3c1d9e204
Revises: a6c9e2f4b817
Create Date: 2026-09-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7f3c1d9e204"
down_revision: str | None = "a6c9e2f4b817"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The figure as the vendor publishes it, e.g. ('INR', '3.00'). Kept as text
    # so it round-trips exactly: this is evidence, and a float would quietly
    # stop matching the page it was read from.
    op.add_column(
        "managed_rate_rules",
        sa.Column("source_currency", sa.String(3), nullable=True),
    )
    op.add_column(
        "managed_rate_rules",
        sa.Column("source_amount", sa.String(), nullable=True),
    )
    # Units of source_currency per 1 USD at conversion time.
    op.add_column(
        "managed_rate_rules", sa.Column("fx_rate", sa.String(), nullable=True)
    )
    # The endpoint the rate came from, so a reader can re-check it rather than
    # trust it.
    op.add_column(
        "managed_rate_rules", sa.Column("fx_source", sa.String(), nullable=True)
    )
    # When WE fetched it, not when the provider published it. The staleness
    # clock starts at the thing we can attest to.
    op.add_column(
        "managed_rate_rules", sa.Column("fx_fetched_at", sa.Float(), nullable=True)
    )


def downgrade() -> None:
    # Additive up, additive down: dropping these would discard the only record
    # of how a live price was derived, and the price itself would survive
    # anyway. A downgrade should not destroy evidence.
    pass
