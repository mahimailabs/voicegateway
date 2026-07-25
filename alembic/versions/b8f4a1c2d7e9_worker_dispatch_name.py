"""add dispatch_name to workers

Revision ID: b8f4a1c2d7e9
Revises: f3c8a1d5e9b2
Create Date: 2026-07-25 10:00:00.000000

The LiveKit ``agent_name`` a worker dispatches under, distinct from the roster
display ``agent_name`` (a VoiceGateway label). Carrying it lets the dashboard
probe a booted-but-idle agent by the exact name LiveKit routes jobs with,
without waiting for a first observed call. Nullable: a worker that reports no
dispatch name (a Pipecat agent, or one not on a LiveKit job) stays valid and is
simply not probeable by name.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b8f4a1c2d7e9"
down_revision: str | None = "f3c8a1d5e9b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("workers", sa.Column("dispatch_name", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("workers", "dispatch_name")
