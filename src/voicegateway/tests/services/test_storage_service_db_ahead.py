"""StorageService keeps writing when the database is ahead of this build.

A file migrated by a newer voicegateway has a superset of the schema this build
knows about, so every table and column it writes to is present. Failing every
write for the life of the process, and re-running alembic to discover that each
time, is strictly worse than logging once and carrying on.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from voicegateway.core.config import GatewayConfig
from voicegateway.core.database import Database
from voicegateway.services.storage_service import StorageService

# Synthetic on purpose; see tests/core/test_database_ahead_of_code.py.
UNKNOWN_REVISION = "0000deadbeef"


async def _db_stamped_ahead(db_path: Path) -> None:
    """A schema at head, stamped at a revision no shipped script defines."""
    db = Database(GatewayConfig(cost_tracking={"db_path": str(db_path)}))
    try:
        await db.run_migrations()
    finally:
        await db.dispose()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE alembic_version_voicegateway SET version_num = ?",
            (UNKNOWN_REVISION,),
        )
        conn.commit()


@pytest.mark.asyncio
async def test_writes_continue_and_warn_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    db_path = tmp_path / "voicegw.db"
    await _db_stamped_ahead(db_path)

    storage = StorageService(db_path)
    try:
        with caplog.at_level(
            logging.DEBUG, logger="voicegateway.services.storage_service"
        ):
            for _ in range(5):
                await storage._ensure_initialized()
    finally:
        await storage.aclose()

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1, f"expected one warning, got {len(warnings)}"
    assert UNKNOWN_REVISION in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_a_real_failure_still_propagates(tmp_path: Path) -> None:
    """Degrading is only for a database ahead of us, not for any failure at all."""
    db_path = tmp_path / "voicegw.db"
    storage = StorageService(db_path)

    boom = RuntimeError("disk on fire")

    async def failing_migrations() -> None:
        raise boom

    storage._conn.run_migrations = failing_migrations  # type: ignore[method-assign]

    try:
        with pytest.raises(RuntimeError, match="disk on fire"):
            await storage._ensure_initialized()
        assert storage._initialized is False
    finally:
        await storage.aclose()
