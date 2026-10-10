"""Memory columns on the workers row: heartbeat round-trip through the migrated schema."""

from __future__ import annotations

from pathlib import Path

from voicegateway.core.config import GatewayConfig
from voicegateway.core.database import Database
from voicegateway.repository import workers_repository as repo


async def _db(tmp_path: Path) -> Database:
    db = Database(GatewayConfig(cost_tracking={"db_path": str(tmp_path / "wm.db")}))
    await db.run_migrations()
    return db


async def test_heartbeat_round_trips_memory(tmp_path) -> None:
    db = await _db(tmp_path)
    presence = {
        "agent_id": "a1",
        "agent_name": "bot",
        "project": "default",
        "tenant_id": None,
        "region": None,
        "version": "0.0.0",
        "host": "h",
        "active_sessions": 0,
        "status": "idle",
        "started_at": 1000.0,
        "memory_rss_bytes": 268_435_456,
        "memory_total_bytes": 536_870_912,
        "ts": 1000.0,
    }
    try:
        async with db.session() as s:
            await repo.upsert_heartbeat(s, presence)
        async with db.session() as s:
            roster = await repo.read_roster(s, tenant_id=None, now=1000.0)
        assert len(roster) == 1
        assert roster[0].memory_rss_bytes == 268_435_456
        assert roster[0].memory_total_bytes == 536_870_912
    finally:
        await db.dispose()
