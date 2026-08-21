"""rate card: per-leg LLM prices

Revision ID: c4d1e8b7a260
Revises: b8e2f04a7d31
Create Date: 2026-08-21 00:00:00.000000

A fixed rate rule carried ONE ``unit_price_usd``, and the rating math applied
it to ``input_units + output_units``. Every LLM provider prices input and
output differently, so no value an operator could type was correct: whichever
rate they entered, the other leg was charged at it.

Three nullable columns, one per leg. Nullable because every existing row is a
single-sided stt/tts rule or a cost-plus markup, and none of them have legs.
``cached_input_price_usd`` stays optional at the application layer too: it
defaults to the input rate, which is what an operator without a negotiated
prompt-cache discount actually pays.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c4d1e8b7a260"
down_revision: str | None = "b8e2f04a7d31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    "input_price_usd",
    "cached_input_price_usd",
    "output_price_usd",
)


def upgrade() -> None:
    for name in _COLUMNS:
        op.add_column(
            "managed_rate_rules",
            sa.Column(name, sa.Float(), nullable=True),
        )


def downgrade() -> None:
    for name in reversed(_COLUMNS):
        op.drop_column("managed_rate_rules", name)
