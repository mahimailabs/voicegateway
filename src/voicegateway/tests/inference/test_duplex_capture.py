"""Native LiveKit metric fixtures: no provider calls or fake billing claims."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from voicegateway.inference.session.capture import MetricCapture, _ttfb_ms
from voicegateway.middleware.cost_tracker_middleware import CostTracker


def test_unknown_first_token_time_is_not_negative_latency():
    assert _ttfb_ms(SimpleNamespace(ttft=-1)) is None
    assert _ttfb_ms(SimpleNamespace(ttft=float("nan"))) is None
    assert _ttfb_ms(SimpleNamespace(ttft=0)) == 0


async def test_backend_tokens_keep_backend_identity():
    from livekit.agents.metrics import LLMMetrics
    from livekit.agents.metrics.base import Metadata

    sink = SimpleNamespace(log_request=AsyncMock())
    capture = MetricCapture(
        cost_tracker=CostTracker(),
        sink=sink,
        project="sdr",
        agent_id="sdr",
        session_id="call-1",
    )
    metric = LLMMetrics(
        request_id="resp-1",
        timestamp=1,
        label="gpt-live",
        duration=0,
        ttft=-1,
        cancelled=False,
        prompt_tokens=20,
        prompt_cached_tokens=0,
        completion_tokens=5,
        total_tokens=25,
        tokens_per_second=0,
        metadata=Metadata(model_name="gpt-4o-mini", model_provider="api.openai.com"),
    )
    capture._record_metric(metric, "llm", "openai", "openai/gpt-live-1")
    await capture.drain()
    record = sink.log_request.call_args.args[0]
    assert record.model_id == "openai/gpt-4o-mini"
    assert record.provider == "openai"
    assert record.input_units == 20
    assert record.ttfb_ms is None
    assert record.cost_usd > 0


async def test_real_duplex_adapter_uses_session_metrics(monkeypatch):
    from livekit.agents import AgentSession
    from livekit.agents.metrics import LLMMetrics
    from livekit.agents.metrics.base import Metadata

    GPTLiveModel = pytest.importorskip("livekit.plugins.openai.realtime").GPTLiveModel

    monkeypatch.setenv("OPENAI_API_KEY", "offline-test-no-network")
    session = AgentSession(llm=GPTLiveModel())
    sink = SimpleNamespace(log_request=AsyncMock())
    capture = MetricCapture(
        cost_tracker=CostTracker(),
        sink=sink,
        project="sdr",
        agent_id="sdr",
        session_id="call-2",
    )
    capture.bind(session)
    metric = LLMMetrics(
        request_id="resp-2",
        timestamp=2,
        label="gpt-live",
        duration=0,
        ttft=-1,
        cancelled=False,
        prompt_tokens=10,
        prompt_cached_tokens=0,
        completion_tokens=2,
        total_tokens=12,
        tokens_per_second=0,
        metadata=Metadata(model_name="gpt-4o-mini", model_provider="api.openai.com"),
    )
    session.emit("metrics_collected", SimpleNamespace(metrics=metric))
    await capture.drain()
    sink.log_request.assert_awaited_once()
    assert sink.log_request.call_args.args[0].model_id == "openai/gpt-4o-mini"


def live_metric(seconds, timestamp=1):
    from livekit.agents.metrics import RealtimeModelMetrics
    from livekit.agents.metrics.base import Metadata

    return RealtimeModelMetrics(
        request_id="live-session",
        timestamp=timestamp,
        label="gpt-live",
        duration=0,
        session_duration=seconds,
        ttft=-1,
        cancelled=False,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        tokens_per_second=0,
        input_token_details=RealtimeModelMetrics.InputTokenDetails(),
        output_token_details=RealtimeModelMetrics.OutputTokenDetails(),
        metadata=Metadata(model_name="gpt-live-1", model_provider="api.openai.com"),
    )


async def test_duration_deltas_and_close_tail_are_priced_once():
    from livekit.agents.metrics.usage import LLMModelUsage

    from voicegateway.accounting.adapters import envelope_from_request_record

    sink = SimpleNamespace(log_request=AsyncMock())
    capture = MetricCapture(
        cost_tracker=CostTracker(),
        sink=sink,
        project="sdr",
        agent_id="sdr",
        session_id="call-3",
    )
    capture._record_metric(live_metric(60), "llm", "unknown", "unknown")
    capture._record_metric(live_metric(60), "llm", "unknown", "unknown")
    capture._record_metric(live_metric(20, 2), "llm", "unknown", "unknown")
    await capture.drain()
    usage = LLMModelUsage(
        provider="api.openai.com", model="gpt-live-1", session_duration=90
    )
    session = SimpleNamespace(usage=SimpleNamespace(model_usage=[usage]))
    await capture.reconcile(session)
    await capture.reconcile(session)
    rows = [call.args[0] for call in sink.log_request.call_args_list]
    assert len(rows) == 3
    assert sum(row.cost_usd for row in rows) == pytest.approx(0.075)
    assert [
        row.metadata["accounting_realtime_quantities"]["audio_seconds"] for row in rows
    ] == [60, 20, 10]
    assert all(row.metadata["pricing_complete"] for row in rows)
    envelope = envelope_from_request_record(rows[0], producer_id="test")
    duration = next(q for q in envelope.quantities if q.dimension == "audio_seconds")
    assert duration.value == "60"
    assert duration.status == "measured"


def test_unknown_duration_model_is_unpriced():
    from voicegateway.inference.pricing.realtime import price_realtime

    assert price_realtime("openai/gpt-live-future", {"audio_seconds": 60}, []) == (
        None,
        "",
    )
    assert price_realtime("openai/gpt-live-1", {"audio_seconds": float("nan")}, []) == (
        None,
        "",
    )


def test_realtime_audio_uses_separate_catalogue_fields(monkeypatch):
    from decimal import Decimal

    from voicegateway.inference.pricing import realtime

    seen = []

    def calculate(usage, model):
        seen.append(usage)
        return Decimal("0.25"), ()

    monkeypatch.setattr(realtime, "price_usage", calculate)
    quantities = {
        "text_input": 10,
        "text_output": 5,
        "cache_read": 2,
        "realtime_audio_input": 100,
        "realtime_audio_output": 40,
        "realtime_audio_cache": 20,
    }
    assert realtime.price_realtime("openai/gpt-realtime", quantities, [])[0] == Decimal(
        "0.25"
    )
    assert seen[0].input_tokens == 110 and seen[0].input_audio_tokens == 100
    assert seen[0].cache_read_tokens == 22 and seen[0].cache_audio_read_tokens == 20
    assert (
        realtime.price_realtime("openai/gpt-realtime", quantities, ["text_input"])[0]
        is None
    )


def test_realtime_catalogue_prices_parent_and_child_buckets():
    from decimal import Decimal

    from voicegateway.inference.pricing.realtime import price_realtime

    total, source = price_realtime(
        "openai/gpt-realtime",
        {
            "text_input": 10,
            "text_output": 5,
            "cache_read": 2,
            "realtime_audio_input": 100,
            "realtime_audio_output": 40,
            "realtime_audio_cache": 20,
        },
        [],
    )
    # Per million: 8*4 + 2*.4 + 80*32 + 20*.4 + 5*16 + 40*64.
    assert total == Decimal("0.0052408")
    assert source.startswith("voice-prices@")


def test_luna_cache_writes_and_long_context_are_priced():
    from decimal import Decimal

    from voicegateway.inference.pricing.realtime import price_realtime

    quantities = {
        "text_input": 1000,
        "text_output": 100,
        "cache_read": 200,
        "cache_write": 300,
    }
    cost, source = price_realtime("openai/gpt-5.6-luna", quantities, [])
    assert cost == Decimal("0.000299")
    assert source == "openai-published:gpt-5.6-luna:2026-10-05"
    assert price_realtime(
        "openai/gpt-5.6-luna", {**quantities, "text_input": 300000}, []
    )[0] == Decimal("0.120138")
    assert (
        price_realtime("openai/gpt-5.6-luna", {**quantities, "text_input": 100}, [])[0]
        is None
    )


async def test_backend_cache_write_reconciliation_and_missing_price():
    from livekit.agents.metrics import LLMMetrics
    from livekit.agents.metrics.base import Metadata
    from livekit.agents.metrics.usage import LLMModelUsage

    sink = SimpleNamespace(log_request=AsyncMock())
    capture = MetricCapture(
        cost_tracker=CostTracker(),
        sink=sink,
        project="sdr",
        agent_id="sdr",
        session_id="cache",
    )
    metric = LLMMetrics(
        request_id="cache",
        timestamp=1,
        label="live",
        duration=0,
        ttft=-1,
        cancelled=False,
        prompt_tokens=1000,
        prompt_cached_tokens=200,
        cache_creation_tokens=300,
        completion_tokens=100,
        total_tokens=1100,
        tokens_per_second=0,
        metadata=Metadata(model_name="gpt-5.6-luna", model_provider="api.openai.com"),
    )
    capture._record_metric(metric, "llm", "openai", "openai/gpt-live-1")
    await capture.drain()
    usage = LLMModelUsage(
        provider="api.openai.com",
        model="gpt-5.6-luna",
        input_tokens=1000,
        input_cached_tokens=200,
        input_cache_creation_tokens=300,
        output_tokens=100,
    )
    await capture.reconcile(SimpleNamespace(usage=SimpleNamespace(model_usage=[usage])))
    sink.log_request.assert_awaited_once()
    record = sink.log_request.call_args.args[0]
    assert record.cost_usd == pytest.approx(0.000299)
    assert record.metadata["accounting_realtime_quantities"]["cache_write"] == 300
    assert record.metadata["pricing_complete"]
    unknown = CostTracker().create_record(
        "openai/unknown-model", "llm", "openai", input_units=100
    )
    assert unknown.metadata["pricing_complete"] is False


def test_realtime_image_usage_is_not_silently_omitted():
    from voicegateway.inference.pricing.realtime import price_realtime
    from voicegateway.inference.session.capture import _accounting_metric_metadata

    metric = live_metric(0)
    metric.metadata.model_name = "gpt-realtime"
    metric.input_token_details.image_tokens = 12
    metadata = _accounting_metric_metadata(metric, "llm")
    assert "image_input" in metadata["accounting_missing_dimensions"]
    assert (
        price_realtime(
            "openai/gpt-realtime",
            metadata["accounting_realtime_quantities"],
            metadata["accounting_missing_dimensions"],
        )[0]
        is None
    )
