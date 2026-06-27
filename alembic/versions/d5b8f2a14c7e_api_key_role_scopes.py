"""api_keys: add role and scopes columns

Revision ID: d5b8f2a14c7e
Revises: d5b8e3a1c9f2
Create Date: 2026-06-27 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d5b8f2a14c7e"
down_revision: str = "d5b8e3a1c9f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.add_column(
            sa.Column(
                "role",
                sa.String(),
                nullable=False,
                server_default="tenant",
            )
        )
        batch_op.add_column(
            sa.Column(
                "scopes",
                sa.String(),
                nullable=False,
                server_default="*",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.drop_column("scopes")
        batch_op.drop_column("role")
