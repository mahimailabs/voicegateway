"""Phase 3, Step 4: the agent_observations rollup table and its columns."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from voicegateway.services.storage_service import StorageService


@pytest.fixture
async def storage(tmp_path):
    s = StorageService(str(tmp_path / "agentobs.db"))
    await s._ensure_initialized()
    return s


async def test_table_exists(storage) -> None:
    async with storage._conn.session() as db:
        result = await db.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='agent_observations'"
            )
        )
        assert result.scalar() == "agent_observations"


async def test_roundtrip_typed_columns(storage) -> None:
    async with storage._conn.session() as db:
        await db.execute(
            text(
                "INSERT INTO agent_observations "
                "(agent_id, request_count, total_cost_usd, error_count, "
                " p50_ms, p95_ms, last_seen, window_start, window_end) "
                "VALUES ('a1', 5, 0.25, 1, 100, 200, 1000.0, 'ws', 'we')"
            )
        )
        await db.commit()
        row = await db.execute(
            text(
                "SELECT agent_id, request_count, total_cost_usd, error_count, "
                "p50_ms, p95_ms, last_seen FROM agent_observations "
                "WHERE agent_id = 'a1'"
            )
        )
        r = row.fetchone()
    assert r == ("a1", 5, 0.25, 1, 100, 200, 1000.0)


async def test_null_agent_id_is_the_unattributed_bucket(storage) -> None:
    async with storage._conn.session() as db:
        await db.execute(
            text(
                "INSERT INTO agent_observations "
                "(agent_id, request_count, total_cost_usd, error_count, "
                " window_start, window_end) "
                "VALUES (NULL, 3, 0.1, 0, 'ws', 'we')"
            )
        )
        await db.commit()
        result = await db.execute(
            text("SELECT request_count FROM agent_observations WHERE agent_id IS NULL")
        )
        assert result.scalar() == 3
