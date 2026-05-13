"""Tests for voicegateway/storage/sqlite.py."""

import time
import uuid

import pytest

from voicegateway.models.request import RequestRecord
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
        await storage.log_request(
            RequestRecord(
                id=str(uuid.uuid4()),
                timestamp=now,
                modality="llm",
                model_id="openai/gpt-4o-mini",
                provider="openai",
                project=project,
                cost_usd=0.01,
            )
        )
    all_rows = await storage.get_recent_requests(limit=10)
    assert len(all_rows) == 2
    alpha_rows = await storage.get_recent_requests(limit=10, project="alpha")
    assert len(alpha_rows) == 1
    assert alpha_rows[0]["project"] == "alpha"


async def test_get_cost_summary(storage):
    now = time.time()
    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=now,
            modality="stt",
            model_id="deepgram/nova-3",
            provider="deepgram",
            cost_usd=0.05,
        )
    )
    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=now,
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            cost_usd=0.10,
        )
    )
    summary = await storage.get_cost_summary("today")
    assert summary["total"] == pytest.approx(0.15, abs=0.001)
    assert "deepgram" in summary["by_provider"]
    assert "openai" in summary["by_provider"]


async def test_get_cost_by_project(storage):
    now = time.time()
    for proj, cost in [("proj-a", 0.05), ("proj-b", 0.10)]:
        await storage.log_request(
            RequestRecord(
                id=str(uuid.uuid4()),
                timestamp=now,
                modality="llm",
                model_id="openai/gpt-4o-mini",
                provider="openai",
                project=proj,
                cost_usd=cost,
            )
        )
    by_project = await storage.get_cost_by_project("today")
    assert "proj-a" in by_project
    assert by_project["proj-a"]["cost"] == pytest.approx(0.05, abs=0.001)


async def test_get_cost_by_modality(storage):
    """STT/LLM/TTS costs aggregate independently by modality."""
    now = time.time()
    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=now,
            modality="stt",
            model_id="deepgram/nova-3",
            provider="deepgram",
            cost_usd=0.05,
        )
    )
    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=now,
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            cost_usd=0.10,
        )
    )
    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=now,
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            cost_usd=0.02,
        )
    )
    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=now,
            modality="tts",
            model_id="cartesia/sonic-3",
            provider="cartesia",
            cost_usd=0.03,
        )
    )
    by_modality = await storage.get_cost_by_modality("today")
    assert by_modality["stt"]["cost"] == pytest.approx(0.05, abs=0.001)
    assert by_modality["stt"]["requests"] == 1
    assert by_modality["llm"]["cost"] == pytest.approx(0.12, abs=0.001)
    assert by_modality["llm"]["requests"] == 2
    assert by_modality["tts"]["cost"] == pytest.approx(0.03, abs=0.001)
    assert by_modality["tts"]["requests"] == 1


async def test_get_cost_by_modality_with_project_filter(storage):
    """Project filter applies to the modality breakdown too."""
    now = time.time()
    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=now,
            modality="stt",
            model_id="deepgram/nova-3",
            provider="deepgram",
            project="alpha",
            cost_usd=0.05,
        )
    )
    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=now,
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            project="beta",
            cost_usd=0.10,
        )
    )
    by_modality = await storage.get_cost_by_modality("today", project="alpha")
    assert by_modality == {
        "stt": {"cost": pytest.approx(0.05, abs=0.001), "requests": 1}
    }


async def test_get_cost_summary_include_pricing_source(storage):
    """`by_model` carries pricing_source attribution when requested."""
    now = time.time()
    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=now,
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            cost_usd=0.10,
            pricing_source="genai-prices@0.0.57",
        )
    )
    summary = await storage.get_cost_summary("today", include_pricing_source=True)
    entry = summary["by_model"]["openai/gpt-4o-mini"]
    assert entry["cost"] == pytest.approx(0.10, abs=0.001)
    assert entry["pricing_source"] == "genai-prices@0.0.57"


async def test_get_cost_summary_pricing_source_omitted_by_default(storage):
    """Default response shape stays stable: no pricing_source key in by_model."""
    now = time.time()
    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=now,
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            cost_usd=0.10,
            pricing_source="genai-prices@0.0.57",
        )
    )
    summary = await storage.get_cost_summary("today")
    entry = summary["by_model"]["openai/gpt-4o-mini"]
    assert "pricing_source" not in entry


async def test_get_cost_summary_pricing_source_concats_distinct(storage):
    """Multiple distinct sources for one model become a comma-joined string."""
    now = time.time()
    for source in ["genai-prices@0.0.57", "genai-prices@0.0.58"]:
        await storage.log_request(
            RequestRecord(
                id=str(uuid.uuid4()),
                timestamp=now,
                modality="llm",
                model_id="openai/gpt-4o-mini",
                provider="openai",
                cost_usd=0.05,
                pricing_source=source,
            )
        )
    summary = await storage.get_cost_summary("today", include_pricing_source=True)
    entry = summary["by_model"]["openai/gpt-4o-mini"]
    sources = sorted(entry["pricing_source"].split(","))
    assert sources == ["genai-prices@0.0.57", "genai-prices@0.0.58"]


async def test_get_cost_summary_explicit_window(storage):
    """`start_ts`/`end_ts` overrides `period`; rows outside the window are excluded."""
    base = time.time()
    # Three records: one well-old, one inside the window, one well-fresh.
    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=base - 86400 * 10,
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            cost_usd=0.05,
        )
    )
    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=base - 86400 * 5,
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            cost_usd=0.10,
        )
    )
    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=base,
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            cost_usd=0.07,
        )
    )
    summary = await storage.get_cost_summary(
        "today",
        start_ts=base - 86400 * 7,
        end_ts=base - 86400 * 2,
    )
    # Only the middle record (5 days back) falls in the window.
    assert summary["total"] == pytest.approx(0.10, abs=0.001)


async def test_get_cost_by_project_explicit_window(storage):
    """Project breakdown respects the explicit window too."""
    base = time.time()
    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=base - 86400 * 10,
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            project="alpha",
            cost_usd=0.05,
        )
    )
    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=base - 86400 * 5,
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            project="alpha",
            cost_usd=0.10,
        )
    )
    by_project = await storage.get_cost_by_project(
        "today",
        start_ts=base - 86400 * 7,
        end_ts=base - 86400 * 2,
    )
    assert by_project["alpha"]["cost"] == pytest.approx(0.10, abs=0.001)
    assert by_project["alpha"]["requests"] == 1


async def test_get_requests_in_window(storage):
    """`get_requests_in_window` returns full per-record rows in chronological order."""
    base = time.time()
    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=base - 86400 * 10,
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            cost_usd=0.05,
            pricing_source="genai-prices@0.0.57",
        )
    )
    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=base - 86400 * 5,
            modality="stt",
            model_id="deepgram/nova-3",
            provider="deepgram",
            cost_usd=0.10,
            pricing_source="local-stt@2026-05-04",
        )
    )
    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=base,
            modality="tts",
            model_id="cartesia/sonic-3",
            provider="cartesia",
            cost_usd=0.07,
            pricing_source="local-tts@2026-05-04",
        )
    )
    rows = await storage.get_requests_in_window(
        start_ts=base - 86400 * 7,
        end_ts=base - 86400 * 2,
    )
    # Only the middle record is in the window.
    assert len(rows) == 1
    assert rows[0]["model_id"] == "deepgram/nova-3"
    assert rows[0]["pricing_source"] == "local-stt@2026-05-04"
    assert rows[0]["cost_usd"] == pytest.approx(0.10, abs=0.001)


async def test_get_requests_in_window_orders_chronologically(storage):
    """`ORDER BY timestamp ASC` so a CSV export reads top-to-bottom in time."""
    base = time.time()
    for offset in (5, 1, 10, 3):
        await storage.log_request(
            RequestRecord(
                id=str(uuid.uuid4()),
                timestamp=base - offset,
                modality="llm",
                model_id="openai/gpt-4o-mini",
                provider="openai",
                cost_usd=0.01,
            )
        )
    rows = await storage.get_requests_in_window(start_ts=base - 100)
    timestamps = [r["timestamp"] for r in rows]
    assert timestamps == sorted(timestamps)


async def test_get_cost_by_modality_explicit_window(storage):
    """Modality breakdown respects the explicit window too."""
    base = time.time()
    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=base - 86400 * 10,
            modality="stt",
            model_id="deepgram/nova-3",
            provider="deepgram",
            cost_usd=0.05,
        )
    )
    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=base - 86400 * 5,
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            cost_usd=0.10,
        )
    )
    by_modality = await storage.get_cost_by_modality(
        "today",
        start_ts=base - 86400 * 7,
        end_ts=base - 86400 * 2,
    )
    assert by_modality == {
        "llm": {"cost": pytest.approx(0.10, abs=0.001), "requests": 1}
    }


async def test_get_project_stats(storage):
    now = time.time()
    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=now,
            modality="stt",
            model_id="deepgram/nova-3",
            provider="deepgram",
            project="my-project",
            cost_usd=0.02,
            ttfb_ms=100.0,
        )
    )
    stats = await storage.get_project_stats("my-project")
    assert stats["requests_today"] >= 1
    assert stats["cost_today"] == pytest.approx(0.02, abs=0.001)


async def test_get_latency_stats(storage):
    now = time.time()
    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=now,
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            ttfb_ms=150.0,
            total_latency_ms=500.0,
        )
    )
    stats = await storage.get_latency_stats("today")
    assert "openai/gpt-4o-mini" in stats
    entry = stats["openai/gpt-4o-mini"]
    assert entry["avg_ttfb_ms"] == pytest.approx(150.0)
    # Single sample: every percentile mirrors the sample value.
    assert entry["ttfb_percentiles"] == {"p50": 150.0, "p95": 150.0, "p99": 150.0}
    assert entry["latency_percentiles"] == {
        "p50": 500.0,
        "p95": 500.0,
        "p99": 500.0,
    }


def _midday_today() -> float:
    """Return a Unix timestamp anchored at 12:00 today."""
    import datetime as _dt

    return _dt.datetime.combine(_dt.date.today(), _dt.time(12, 0)).timestamp()


async def test_get_latency_stats_percentiles_with_many_samples(storage):
    """Log 100 requests per model and assert server-side p50/p95/p99."""
    now = _midday_today()
    for i in range(1, 101):
        await storage.log_request(
            RequestRecord(
                id=str(uuid.uuid4()),
                timestamp=now - i,
                modality="llm",
                model_id="openai/gpt-4o",
                provider="openai",
                ttfb_ms=float(i),
                total_latency_ms=float(i * 2),
            )
        )

    stats = await storage.get_latency_stats("today")
    ttfb = stats["openai/gpt-4o"]["ttfb_percentiles"]
    # Linear-interp percentiles of [1..100]: p50=50.5, p95=95.05, p99=99.01
    assert ttfb["p50"] == pytest.approx(50.5)
    assert ttfb["p95"] == pytest.approx(95.05)
    assert ttfb["p99"] == pytest.approx(99.01)


async def test_get_latency_stats_custom_percentiles(storage):
    """Caller-supplied percentiles override the defaults."""
    now = _midday_today()
    for i in range(1, 21):
        await storage.log_request(
            RequestRecord(
                id=str(uuid.uuid4()),
                timestamp=now - i,
                modality="llm",
                model_id="openai/gpt-4o",
                provider="openai",
                ttfb_ms=float(i),
                total_latency_ms=float(i),
            )
        )
    stats = await storage.get_latency_stats("today", percentiles=[25.0, 75.0])
    ttfb = stats["openai/gpt-4o"]["ttfb_percentiles"]
    assert set(ttfb.keys()) == {"p25", "p75"}


async def test_get_latency_samples(storage):
    """Raw-sample accessor returns (ttfb, total) lists."""
    now = _midday_today()
    for i in range(1, 6):
        await storage.log_request(
            RequestRecord(
                id=str(uuid.uuid4()),
                timestamp=now - i,
                modality="stt",
                model_id="deepgram/nova-3",
                provider="deepgram",
                ttfb_ms=float(i * 10),
                total_latency_ms=float(i * 20),
            )
        )
    ttfb, total = await storage.get_latency_samples("today")
    assert sorted(ttfb) == [10.0, 20.0, 30.0, 40.0, 50.0]
    assert sorted(total) == [20.0, 40.0, 60.0, 80.0, 100.0]


async def test_get_latency_samples_modality_filter(storage):
    """modality= arg drops samples from other modalities."""
    now = _midday_today()
    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=now - 1,
            modality="stt",
            model_id="deepgram/nova-3",
            provider="deepgram",
            ttfb_ms=100.0,
            total_latency_ms=200.0,
        )
    )
    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=now - 2,
            modality="llm",
            model_id="openai/gpt-4o",
            provider="openai",
            ttfb_ms=300.0,
            total_latency_ms=600.0,
        )
    )
    stt_ttfb, _ = await storage.get_latency_samples("today", modality="stt")
    llm_ttfb, _ = await storage.get_latency_samples("today", modality="llm")
    assert stt_ttfb == [100.0]
    assert llm_ttfb == [300.0]


async def test_get_latency_stats_includes_models_without_ttfb(storage):
    """Rows with only total_latency_ms still appear with latency_percentiles."""
    now = _midday_today()
    for i in range(1, 6):
        await storage.log_request(
            RequestRecord(
                id=str(uuid.uuid4()),
                timestamp=now - i,
                modality="tts",
                model_id="local/piper",
                provider="piper",
                ttfb_ms=None,  # local TTS may not report TTFB
                total_latency_ms=float(i * 30),
            )
        )
    stats = await storage.get_latency_stats("today")
    assert "local/piper" in stats
    entry = stats["local/piper"]
    # No TTFB samples → percentiles all None.
    assert entry["ttfb_percentiles"]["p50"] is None
    # Total-latency percentiles come from the 5 samples.
    assert entry["latency_percentiles"]["p50"] is not None
