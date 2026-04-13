"""Tests for cost tracking."""

import pytest

from voicegateway.middleware.cost_tracker import CostTracker
from voicegateway.storage.sqlite import SQLiteStorage


def test_stt_cost_calculation():
    tracker = CostTracker()
    cost = tracker.calculate_cost("deepgram/nova-3", "stt", input_units=1.0)
    assert cost == 0.0043

    cost = tracker.calculate_cost("deepgram/nova-3", "stt", input_units=2.5)
    assert cost == pytest.approx(0.01075)


def test_llm_cost_calculation():
    tracker = CostTracker()
    cost = tracker.calculate_cost(
        "openai/gpt-4.1-mini", "llm", input_units=1000, output_units=500
    )
    # 1000 tokens * 0.0004/1k + 500 tokens * 0.0016/1k = 0.0004 + 0.0008
    assert cost == pytest.approx(0.0012)


def test_tts_cost_calculation():
    tracker = CostTracker()
    cost = tracker.calculate_cost("cartesia/sonic-3", "tts", input_units=100)
    assert cost == pytest.approx(0.0065)


def test_local_model_is_free():
    tracker = CostTracker()
    cost = tracker.calculate_cost("ollama/qwen2.5:3b", "llm", input_units=10000, output_units=5000)
    assert cost == 0.0

    cost = tracker.calculate_cost("local/whisper-large-v3", "stt", input_units=60)
    assert cost == 0.0


def test_unknown_model_cost_zero():
    tracker = CostTracker()
    cost = tracker.calculate_cost("unknown/model", "stt", input_units=5)
    assert cost == 0.0


@pytest.mark.asyncio
async def test_log_and_query_request(tmp_path):
    db = tmp_path / "test.db"
    storage = SQLiteStorage(str(db))
    tracker = CostTracker(storage)

    record = tracker.create_record(
        model_id="deepgram/nova-3",
        modality="stt",
        provider="deepgram",
        input_units=1.5,
        ttfb_ms=100.0,
        total_latency_ms=2000.0,
    )
    await tracker.log_request(record)

    summary = await storage.get_cost_summary("today")
    assert summary["total"] > 0
    assert "deepgram" in summary["by_provider"]
    assert summary["by_provider"]["deepgram"]["requests"] == 1


@pytest.mark.asyncio
async def test_cost_summary_by_model(tmp_path):
    db = tmp_path / "test2.db"
    storage = SQLiteStorage(str(db))
    tracker = CostTracker(storage)

    for i in range(3):
        record = tracker.create_record(
            model_id="deepgram/nova-3",
            modality="stt",
            provider="deepgram",
            input_units=1.0,
        )
        await tracker.log_request(record)

    summary = await storage.get_cost_summary("today")
    assert summary["by_model"]["deepgram/nova-3"]["requests"] == 3
    assert summary["total"] == pytest.approx(0.0043 * 3)
