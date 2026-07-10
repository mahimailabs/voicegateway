"""add rated_price_usd + rate_rule to requests

Revision ID: c7d2a9f1e6b4
Revises: e9b3d7c2a5f1
Create Date: 2026-07-10 09:00:00.000000

VoiceGateway is the rating layer: each request carries a billable price
(``rated_price_usd``) derived from the rate card in effect at write time,
plus an auditable ``rate_rule`` token naming the rule that produced it
(``cost_plus:1.3``, ``fixed:0.006/minute``, ``default:1``). Stamping the
price onto the row keeps revenue immutable: later rate-card edits never
retroactively change historical billing.

Nullable with server defaults (0 / '') so existing rows backfill cleanly
and INSERTs that omit the columns stay valid.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c7d2a9f1e6b4"
down_revision: str | None = "e9b3d7c2a5f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Direct add_column (not batch_alter_table): a batch rename would break
    # any view referencing ``requests`` (daily_costs, project_daily_costs).
    op.add_column(
        "requests",
        sa.Column(
            "rated_price_usd",
            sa.Float(),
            nullable=True,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "requests",
        sa.Column(
            "rate_rule",
            sa.Text(),
            nullable=True,
            server_default=sa.text("''"),
        ),
    )


def downgrade() -> None:
    # SQLite 3.35+ supports DROP COLUMN directly; same view-collision
    # rationale as upgrade().
    op.drop_column("requests", "rate_rule")
    op.drop_column("requests", "rated_price_usd")
