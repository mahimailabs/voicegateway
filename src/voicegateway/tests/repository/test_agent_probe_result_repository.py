"""The probe-result cache: upsert (overwrite) + read, and bad-JSON tolerance."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from voicegateway.models.agent_probe_result_model import (  # noqa: F401 - registers table
    AgentProbeResult,
)
from voicegateway.repository import agent_probe_result_repository as repo


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


async def test_upsert_then_read_round_trips(db: AsyncSession) -> None:
    result = {"components": {"stt": 0.5}, "cost_usd": 0.004, "error": None}
    await repo.upsert_probe_result(db, "a1", result, created_at=1000.0)

    out = await repo.read_probe_results(db, ["a1"])
    assert out["a1"]["result"] == result
    assert out["a1"]["created_at"] == 1000.0


async def test_upsert_overwrites_the_previous_result(db: AsyncSession) -> None:
    """One row per agent: a new probe replaces the last, it does not accumulate."""
    await repo.upsert_probe_result(db, "a1", {"cost_usd": 0.001}, created_at=1000.0)
    await repo.upsert_probe_result(db, "a1", {"cost_usd": 0.009}, created_at=2000.0)

    out = await repo.read_probe_results(db)
    assert len(out) == 1
    assert out["a1"]["result"] == {"cost_usd": 0.009}
    assert out["a1"]["created_at"] == 2000.0


async def test_read_filters_by_agent_ids(db: AsyncSession) -> None:
    await repo.upsert_probe_result(db, "a1", {"x": 1}, created_at=1.0)
    await repo.upsert_probe_result(db, "a2", {"x": 2}, created_at=1.0)
    out = await repo.read_probe_results(db, ["a1"])
    assert set(out) == {"a1"}


async def test_unparseable_row_is_skipped_not_raised(db: AsyncSession) -> None:
    """One corrupt cache entry must not break the whole fleet index read."""
    await db.execute(
        text(
            "INSERT INTO agent_probe_results (agent_id, result_json, created_at) "
            "VALUES ('bad', 'not json{', 1.0)"
        )
    )
    await repo.upsert_probe_result(db, "good", {"ok": True}, created_at=2.0)
    await db.commit()
    out = await repo.read_probe_results(db)
    assert set(out) == {"good"}  # 'bad' skipped, not an exception
