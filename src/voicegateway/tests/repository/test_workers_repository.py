"""TDD for workers_repository: heartbeat upsert + roster liveness."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from voicegateway.models.worker_model import Worker  # noqa: F401 - registers table
from voicegateway.repository import workers_repository as workers


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    session = AsyncSession(engine)
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


def _presence(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "agent_id": "agent-a",
        "agent_name": "Agent A",
        "project": "default",
        "tenant_id": "acme",
        "region": "us-east",
        "version": "1.0.0",
        "host": "host-1",
        "active_sessions": 3,
        "status": "busy",
        "started_at": 1000.0,
        "ts": 2000.0,
    }
    base.update(overrides)
    return base


async def test_upsert_then_read_fresh_is_busy(db: AsyncSession) -> None:
    await workers.upsert_heartbeat(db, _presence())

    rows = await workers.read_roster(db, tenant_id="acme", now=2010.0)

    assert len(rows) == 1
    row = rows[0]
    assert row.agent_id == "agent-a"
    assert row.status == "busy"  # fresh heartbeat keeps the reported status
    assert row.active_sessions == 3  # preserved from the payload


async def test_upsert_twice_same_key_keeps_one_row_latest_wins(
    db: AsyncSession,
) -> None:
    await workers.upsert_heartbeat(
        db, _presence(active_sessions=1, status="idle", ts=2000.0)
    )
    await workers.upsert_heartbeat(
        db, _presence(active_sessions=5, status="busy", ts=2100.0)
    )

    rows = await workers.read_roster(db, tenant_id="acme", now=2110.0)

    assert len(rows) == 1  # (tenant_id, agent_id) is a single upserted row
    assert rows[0].active_sessions == 5
    assert rows[0].status == "busy"
    assert rows[0].last_seen == 2100.0


async def test_stale_last_seen_is_offline(db: AsyncSession) -> None:
    await workers.upsert_heartbeat(db, _presence(ts=1000.0, status="busy"))

    # now is 100s past the last heartbeat, beyond the 45s TTL.
    rows = await workers.read_roster(db, tenant_id="acme", now=1100.0, ttl_seconds=45.0)

    assert len(rows) == 1
    assert rows[0].status == "offline"
