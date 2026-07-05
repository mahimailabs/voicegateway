"""The ``workers`` table registers and creates cleanly."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

from voicegateway.models.worker_model import Worker


async def test_workers_table_creates() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
    finally:
        await engine.dispose()

    assert Worker.__tablename__ == "workers"
    assert "workers" in SQLModel.metadata.tables
