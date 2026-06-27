"""Tests for the first-class ``tenants`` table (Task 2).

REQ-VG-TENANT-003: tenants table exists, is seeded with 'default' row.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from voicegateway.core.config import GatewayConfig
from voicegateway.core.database import Database
from voicegateway.inference.session import DEFAULT_TENANT


async def _build_db(tmp_path: Path) -> Database:
    db_path = tmp_path / "tenants-table.db"
    db = Database(GatewayConfig(cost_tracking={"db_path": str(db_path)}))
    await db.run_migrations()
    return db


async def test_tenants_table_exists_and_seeds_default(tmp_path: Path) -> None:
    """After running migrations, tenants table must exist and contain 'default'."""
    db = await _build_db(tmp_path)
    sync_engine_url = f"sqlite:///{db.db_file_path}"
    import sqlalchemy as sa

    engine = sa.create_engine(sync_engine_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(sa.text("SELECT tenant_id, status FROM tenants")).all()
    finally:
        engine.dispose()
        await db.dispose()

    assert len(rows) == 1, f"expected 1 seeded row, got {len(rows)}"
    tenant_id, status = rows[0]
    assert tenant_id == "default", f"expected tenant_id='default', got {tenant_id!r}"
    assert status == "active", f"expected status='active', got {status!r}"


async def test_tenants_table_included_in_migration_set(tmp_path: Path) -> None:
    """Confirm 'tenants' appears in the list of tables after migrations."""
    db = await _build_db(tmp_path)
    db_path = db.db_file_path
    await db.dispose()

    with sqlite3.connect(str(db_path)) as conn:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "tenants" in names, f"'tenants' table missing; found: {sorted(names)}"


def test_default_tenant_constant() -> None:
    """DEFAULT_TENANT exported from voicegateway.inference.session must equal 'default'."""
    assert DEFAULT_TENANT == "default"
