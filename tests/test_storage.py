"""Tests for voicegateway/storage/sqlite.py."""

import time
import uuid

import pytest

from voicegateway.storage.models import RequestRecord
from voicegateway.storage.sqlite import SQLiteStorage


@pytest.fixture
async def storage(tmp_path):
    return SQLiteStorage(str(tmp_path / "test.db"))


async def test_init_creates_tables(storage):
    """Initializing storage creates the requests table and indexes."""
    conn = await storage._ensure_initialized()
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='requests'"
    )
    row = await cursor.fetchone()
    assert row is not None


async def test_log_request(storage):
    record = RequestRecord(
        id=str(uuid.uuid4()),
        timestamp=time.time(),
        modality="stt",
        model_id="deepgram/nova-3",
        provider="deepgram",
        project="test",
        cost_usd=0.01,
        ttfb_ms=100.0,
        total_latency_ms=200.0,
    )
    await storage.log_request(record)
    rows = await storage.get_recent_requests(limit=10)
    assert len(rows) == 1
    assert rows[0]["model_id"] == "deepgram/nova-3"


async def test_get_recent_requests_with_project_filter(storage):
    now = time.time()
    for project in ["alpha", "beta"]:
        await storage.log_request(RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=now,
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            project=project,
            cost_usd=0.01,
        ))
    all_rows = await storage.get_recent_requests(limit=10)
    assert len(all_rows) == 2
    alpha_rows = await storage.get_recent_requests(limit=10, project="alpha")
    assert len(alpha_rows) == 1
    assert alpha_rows[0]["project"] == "alpha"


async def test_get_cost_summary(storage):
    now = time.time()
    await storage.log_request(RequestRecord(
        id=str(uuid.uuid4()), timestamp=now, modality="stt",
        model_id="deepgram/nova-3", provider="deepgram", cost_usd=0.05,
    ))
    await storage.log_request(RequestRecord(
        id=str(uuid.uuid4()), timestamp=now, modality="llm",
        model_id="openai/gpt-4o-mini", provider="openai", cost_usd=0.10,
    ))
    summary = await storage.get_cost_summary("today")
    assert summary["total"] == pytest.approx(0.15, abs=0.001)
    assert "deepgram" in summary["by_provider"]
    assert "openai" in summary["by_provider"]


async def test_get_cost_by_project(storage):
    now = time.time()
    for proj, cost in [("proj-a", 0.05), ("proj-b", 0.10)]:
        await storage.log_request(RequestRecord(
            id=str(uuid.uuid4()), timestamp=now, modality="llm",
            model_id="openai/gpt-4o-mini", provider="openai",
            project=proj, cost_usd=cost,
        ))
    by_project = await storage.get_cost_by_project("today")
    assert "proj-a" in by_project
    assert by_project["proj-a"]["cost"] == pytest.approx(0.05, abs=0.001)


async def test_get_project_stats(storage):
    now = time.time()
    await storage.log_request(RequestRecord(
        id=str(uuid.uuid4()), timestamp=now, modality="stt",
        model_id="deepgram/nova-3", provider="deepgram",
        project="my-project", cost_usd=0.02, ttfb_ms=100.0,
    ))
    stats = await storage.get_project_stats("my-project")
    assert stats["requests_today"] >= 1
    assert stats["cost_today"] == pytest.approx(0.02, abs=0.001)


async def test_get_latency_stats(storage):
    now = time.time()
    await storage.log_request(RequestRecord(
        id=str(uuid.uuid4()), timestamp=now, modality="llm",
        model_id="openai/gpt-4o-mini", provider="openai",
        ttfb_ms=150.0, total_latency_ms=500.0,
    ))
    stats = await storage.get_latency_stats("today")
    assert "openai/gpt-4o-mini" in stats
    assert stats["openai/gpt-4o-mini"]["avg_ttfb_ms"] == pytest.approx(150.0)
