"""rate card: which ledger side a rule sets

Revision ID: d7a2f91c4b03
Revises: c4d1e8b7a260
Create Date: 2026-08-21 12:00:00.000000

A rate rule could only ever set what a tenant is CHARGED. The cost it marked
up was always the voice-prices catalogue figure, which is a published list
price, and anyone at volume is on a negotiated contract that differs from it
by a margin nobody outside the contract can see. So ``cost_plus`` multiplied a
number that was never true and produced a margin the operator could not tell
was wrong.

``sets`` names the side: ``price`` (the historical behaviour, and the default
for every existing row) or ``cost`` (what the operator actually pays,
replacing the catalogue figure).

NOT NULL with a server default rather than nullable, because "which side does
this rule set" has no meaningful unknown state: a rule written before this
column existed set the price, definitionally, since that was the only thing a
rule could do.

The primary key is unchanged for price rules. ``scope_key`` prefixes only cost
rules, so every existing ``rule_id`` still addresses its row and an upsert
against it updates rather than duplicating. That is also what lets a cost rule
and a price rule coexist at one scope, which is the ordinary configuration.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d7a2f91c4b03"
down_revision: str | None = "c4d1e8b7a260"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "managed_rate_rules",
        sa.Column(
            "sets",
            sa.String(),
            nullable=False,
            server_default="price",
        ),
    )


def downgrade() -> None:
    op.drop_column("managed_rate_rules", "sets")
