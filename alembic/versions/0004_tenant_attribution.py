"""Tenant attribution: virtual_keys table + tenant_id on every relevant table.

Ported from ``voicegateway/storage/migrations/0005_tenant_attribution.py``.
The OQ4 conditional-table pattern survives the port: v0.0.5-only DBs
without the v0.2.0 (turns/dead_air_events) or v0.3.0 (replay_*) tables
silently skip those tenant_id ALTERs.

Revision ID: 0004_tenant_attribution
Revises: 0003_replay_tables
Create Date: 2026-05-14
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0004_tenant_attribution"
down_revision: str | None = "0003_replay_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_BASELINE_TENANT_TABLES = ("sessions", "requests")
_CONDITIONAL_TENANT_TABLES = (
    "turns",
    "dead_air_events",
    "replay_stt_events",
    "replay_llm_tokens",
    "replay_tts_frames",
    "replay_state_snapshots",
)


_VIRTUAL_KEYS_TABLE = """
CREATE TABLE IF NOT EXISTS virtual_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key_prefix TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    tenant_id TEXT,
    issued_by TEXT,
    issued_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_used_at TEXT,
    revoked_at TEXT
)
"""
_VIRTUAL_KEYS_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_virtual_keys_prefix ON virtual_keys(key_prefix)",
    "CREATE INDEX IF NOT EXISTS idx_virtual_keys_tenant_id ON virtual_keys(tenant_id)",
)


def _table_exists(table: str) -> bool:
    bind = op.get_bind()
    row = bind.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:n"),
        {"n": table},
    ).fetchone()
    return row is not None


def _existing_columns(table: str) -> set[str]:
    bind = op.get_bind()
    rows = bind.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {row[1] for row in rows}


def _add_tenant_id(table: str) -> None:
    if "tenant_id" not in _existing_columns(table):
        op.execute(f"ALTER TABLE {table} ADD COLUMN tenant_id TEXT")


def upgrade() -> None:
    op.execute(_VIRTUAL_KEYS_TABLE)
    for stmt in _VIRTUAL_KEYS_INDEXES:
        op.execute(stmt)

    for table in _BASELINE_TENANT_TABLES:
        _add_tenant_id(table)

    for table in _CONDITIONAL_TENANT_TABLES:
        if _table_exists(table):
            _add_tenant_id(table)


def downgrade() -> None:
    """No-op: SQLite migrations are forward-only."""
