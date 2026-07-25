"""TDD for transcript_turns_repository: replace-on-write + ordered read."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from voicegateway.models.transcript_turn_model import (  # noqa: F401 - registers table
    TranscriptTurn,
)
from voicegateway.repository import transcript_turns_repository as tr


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


async def test_create_and_list_preserves_order(db):
    n = await tr.create_transcript_bulk(
        db, "s1", [("user", "hi"), ("agent", "hello"), ("user", "bye")]
    )
    assert n == 3
    rows = await tr.list_transcript_by_session(db, "s1")
    assert [(r.seq, r.role, r.text) for r in rows] == [
        (0, "user", "hi"),
        (1, "agent", "hello"),
        (2, "user", "bye"),
    ]


async def test_rewrite_replaces_not_appends(db):
    await tr.create_transcript_bulk(db, "s1", [("user", "first")])
    await tr.create_transcript_bulk(db, "s1", [("user", "again"), ("agent", "ok")])
    rows = await tr.list_transcript_by_session(db, "s1")
    assert [(r.role, r.text) for r in rows] == [("user", "again"), ("agent", "ok")]


async def test_empty_turns_clears(db):
    await tr.create_transcript_bulk(db, "s1", [("user", "hi")])
    n = await tr.create_transcript_bulk(db, "s1", [])
    assert n == 0
    assert await tr.list_transcript_by_session(db, "s1") == []


async def test_scoped_to_session(db):
    await tr.create_transcript_bulk(db, "s1", [("user", "a")])
    await tr.create_transcript_bulk(db, "s2", [("user", "b")])
    assert len(await tr.list_transcript_by_session(db, "s1")) == 1
    assert len(await tr.list_transcript_by_session(db, "s2")) == 1
