"""merge transcript_turns and agent_probe_results heads

Revision ID: 06836270c254
Revises: b7d2f4a9c1e3, d2f6b8a1c3e5
Create Date: 2026-07-25 19:05:24.608573
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlmodel  # noqa: F401 — generated migrations may reference SQLModel types

revision: str = "06836270c254"
# Two parents: this migration merges the transcript_turns (#151) and
# agent_probe_results (#153) heads, which both branched off worker_memory.
down_revision: str | Sequence[str] | None = ("b7d2f4a9c1e3", "d2f6b8a1c3e5")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
