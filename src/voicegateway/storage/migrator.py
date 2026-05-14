"""Schema bootstrap + idempotent migration runner for SQLite storage."""

from __future__ import annotations

import logging
from importlib import import_module
from typing import TYPE_CHECKING

from voicegateway.storage.schema import AUDIT_LOG_SCHEMA, SCHEMA_SQL

if TYPE_CHECKING:
    import aiosqlite

    from voicegateway.storage.connection import ConnectionManager

_logger = logging.getLogger(__name__)

_MIGRATION_MODULE_PATHS = (
    "voicegateway.storage.migrations.0003_turns_and_deadair",
    "voicegateway.storage.migrations.0004_replay_tables",
    "voicegateway.storage.migrations.0005_tenant_attribution",
    "voicegateway.storage.migrations.0006_routing_and_branding",
    "voicegateway.storage.migrations.0007_guardrails",
)


async def initialize(db: aiosqlite.Connection, manager: ConnectionManager) -> None:
    """Run schema + every numbered migration once per ConnectionManager."""
    if manager.is_initialized:
        return

    await db.executescript(SCHEMA_SQL)

    await _migrate_requests_table(db)
    await _migrate_managed_providers(db)
    await _ensure_managed_table_indexes(db)
    await _create_audit_log(db)
    await _migrate_plaintext_keys(db)

    for path in _MIGRATION_MODULE_PATHS:
        module = import_module(path)
        await module.apply(db)

    await db.commit()
    manager.mark_initialized()


async def _migrate_requests_table(db: aiosqlite.Connection) -> None:
    """Backfill project / pricing_source / session_id columns on legacy DBs."""
    cursor = await db.execute("PRAGMA table_info(requests)")
    cols = [row[1] async for row in cursor]
    if "project" not in cols:
        await db.execute(
            "ALTER TABLE requests ADD COLUMN project TEXT NOT NULL DEFAULT 'default'"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_requests_project ON requests(project)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_requests_project_timestamp "
            "ON requests(project, timestamp)"
        )
    if "pricing_source" not in cols:
        await db.execute(
            "ALTER TABLE requests ADD COLUMN pricing_source TEXT NOT NULL DEFAULT ''"
        )
    if "session_id" not in cols:
        await db.execute("ALTER TABLE requests ADD COLUMN session_id TEXT")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_requests_session_id ON requests(session_id)"
    )


async def _migrate_managed_providers(db: aiosqlite.Connection) -> None:
    """Backfill the project column on managed_providers if missing."""
    cursor = await db.execute("PRAGMA table_info(managed_providers)")
    cols = [row[1] async for row in cursor]
    if "project" not in cols:
        await db.execute("ALTER TABLE managed_providers ADD COLUMN project TEXT")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_managed_providers_project "
        "ON managed_providers(project)"
    )


async def _ensure_managed_table_indexes(db: aiosqlite.Connection) -> None:
    """Create the secondary indexes on managed_providers / managed_models."""
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_managed_providers_type "
        "ON managed_providers(provider_type)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_managed_models_modality "
        "ON managed_models(modality)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_managed_models_provider "
        "ON managed_models(provider_id)"
    )


async def _create_audit_log(db: aiosqlite.Connection) -> None:
    """Create the config_audit_log table + its two indexes."""
    await db.execute(AUDIT_LOG_SCHEMA)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON config_audit_log(timestamp)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_entity "
        "ON config_audit_log(entity_type, entity_id)"
    )


async def _migrate_plaintext_keys(db: aiosqlite.Connection) -> None:
    """Encrypt any plaintext API keys left over from before encryption was added."""
    from voicegateway.core.crypto import encrypt, is_fernet_token

    cursor = await db.execute(
        "SELECT provider_id, api_key_encrypted FROM managed_providers "
        "WHERE api_key_encrypted != ''"
    )
    rows = await cursor.fetchall()
    migrated = 0
    for row in rows:
        provider_id, raw_key = row[0], row[1]
        if not is_fernet_token(raw_key):
            encrypted = encrypt(raw_key)
            await db.execute(
                "UPDATE managed_providers SET api_key_encrypted = ? "
                "WHERE provider_id = ?",
                (encrypted, provider_id),
            )
            migrated += 1
    if migrated:
        _logger.warning(
            "Migrated %d plaintext API key(s) to encrypted storage.", migrated
        )


__all__ = ["initialize"]
