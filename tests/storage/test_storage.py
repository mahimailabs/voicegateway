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
    entry = stats["openai/gpt-4o-mini"]
    assert entry["avg_ttfb_ms"] == pytest.approx(150.0)
    # Single sample: every percentile mirrors the sample value.
    assert entry["ttfb_percentiles"] == {"p50": 150.0, "p95": 150.0, "p99": 150.0}
    assert entry["latency_percentiles"] == {
        "p50": 500.0, "p95": 500.0, "p99": 500.0,
    }


async def test_get_latency_stats_percentiles_with_many_samples(storage):
    """Log 100 requests per model and assert server-side p50/p95/p99."""
    now = time.time()
    for i in range(1, 101):
        await storage.log_request(RequestRecord(
            id=str(uuid.uuid4()), timestamp=now - i,
            modality="llm", model_id="openai/gpt-4o",
            provider="openai",
            ttfb_ms=float(i),
            total_latency_ms=float(i * 2),
        ))

    stats = await storage.get_latency_stats("today")
    ttfb = stats["openai/gpt-4o"]["ttfb_percentiles"]
    # Linear-interp percentiles of [1..100]: p50=50.5, p95=95.05, p99=99.01
    assert ttfb["p50"] == pytest.approx(50.5)
    assert ttfb["p95"] == pytest.approx(95.05)
    assert ttfb["p99"] == pytest.approx(99.01)


async def test_get_latency_stats_custom_percentiles(storage):
    """Caller-supplied percentiles override the defaults."""
    now = time.time()
    for i in range(1, 21):
        await storage.log_request(RequestRecord(
            id=str(uuid.uuid4()), timestamp=now - i,
            modality="llm", model_id="openai/gpt-4o",
            provider="openai", ttfb_ms=float(i), total_latency_ms=float(i),
        ))
    stats = await storage.get_latency_stats(
        "today", percentiles=[25.0, 75.0]
    )
    ttfb = stats["openai/gpt-4o"]["ttfb_percentiles"]
    assert set(ttfb.keys()) == {"p25", "p75"}


async def test_get_latency_samples(storage):
    """Raw-sample accessor returns (ttfb, total) lists."""
    now = time.time()
    for i in range(1, 6):
        await storage.log_request(RequestRecord(
            id=str(uuid.uuid4()), timestamp=now - i,
            modality="stt", model_id="deepgram/nova-3",
            provider="deepgram",
            ttfb_ms=float(i * 10),
            total_latency_ms=float(i * 20),
        ))
    ttfb, total = await storage.get_latency_samples("today")
    assert sorted(ttfb) == [10.0, 20.0, 30.0, 40.0, 50.0]
    assert sorted(total) == [20.0, 40.0, 60.0, 80.0, 100.0]
