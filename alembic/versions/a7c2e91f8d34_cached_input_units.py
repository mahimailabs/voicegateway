"""add cached_input_units to requests

Revision ID: a7c2e91f8d34
Revises: f1ae43d7fa98
Create Date: 2026-05-27 05:00:00.000000

LK reports ``LLMMetrics.prompt_cached_tokens`` separately from
``prompt_tokens`` (cached is a subset). genai-prices applies a
per-provider discount (OpenAI 50%, Anthropic ~10%) when cached tokens
are passed as ``cache_read_tokens``. Tracking the raw cached count on
the request row keeps reconcile auditable and lets the dashboard
break spend down by cached vs fresh input.

Nullable with server default 0 so existing rows backfill cleanly and
new INSERTs without the field stay valid.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7c2e91f8d34"
down_revision: str | None = "f1ae43d7fa98"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite supports ALTER TABLE ADD COLUMN directly; using batch_alter
    # triggers a full table rename, which breaks any view referencing
    # ``requests`` (e.g. ``daily_costs``). Direct add_column avoids that.
    op.add_column(
        "requests",
        sa.Column(
            "cached_input_units",
            sa.Float(),
            nullable=True,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    # SQLite 3.35+ supports DROP COLUMN directly. Same view-collision
    # rationale as upgrade(): skip batch_alter_table.
    op.drop_column("requests", "cached_input_units")
