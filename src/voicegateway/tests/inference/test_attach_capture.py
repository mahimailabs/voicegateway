"""Tests for voicegateway.attach() per-component metric capture."""

from __future__ import annotations

from typing import Any

from voicegateway.inference.session.capture import MetricCapture, units_from_metric
from voicegateway.middleware.cost_tracker_middleware import CostTracker
from voicegateway.services.sinks import LocalSqliteSink
from voicegateway.services.storage_service import StorageService


class _LLMMetric:
    prompt_tokens = 1000
    completion_tokens = 500
    prompt_cached_tokens = 200
    ttft = 0.25


class _STTMetric:
    audio_duration = 120.0  # seconds


class _TTSMetric:
    characters_count = 350
    ttfb = 0.1


class _FakeEmitter:
    """Minimal stand-in for a LiveKit plugin instance (event emitter)."""

    def __init__(
        self, *, model: str | None = None, provider: str | None = None
    ) -> None:
        self.model = model
        self.provider = provider
        self._handlers: dict[str, list[Any]] = {}

    def on(self, event: str, handler: Any) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def emit(self, event: str, *args: Any) -> None:
        for handler in list(self._handlers.get(event, [])):
            handler(*args)


class _FakeSession:
    """Minimal stand-in for livekit.agents.AgentSession."""

    def __init__(
        self,
        *,
        stt: Any = None,
        llm: Any = None,
        tts: Any = None,
    ) -> None:
        self.stt = stt
        self.llm = llm
        self.tts = tts
        self._handlers: dict[str, list[Any]] = {}

    def on(self, event: str, handler: Any) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def emit(self, event: str, *args: Any) -> None:
        for handler in list(self._handlers.get(event, [])):
            handler(*args)


# --- unit mapping --------------------------------------------------------


def test_units_from_llm_metric():
    inp, out, cached, ttfb_ms = units_from_metric(_LLMMetric(), "llm")
    assert inp == 1000.0
    assert out == 500.0
    assert cached == 200.0
    assert ttfb_ms == 250.0


def test_units_from_stt_metric_converts_seconds_to_minutes():
    inp, out, cached, ttfb_ms = units_from_metric(_STTMetric(), "stt")
    assert inp == 2.0  # 120s / 60 -> minutes (cost path multiplies back by 60)
    assert out == 0.0
    assert cached == 0.0
    assert ttfb_ms is None


def test_units_from_tts_metric():
    inp, out, cached, ttfb_ms = units_from_metric(_TTSMetric(), "tts")
    assert inp == 350.0
    assert out == 0.0
    assert ttfb_ms == 100.0


# --- capture binding -----------------------------------------------------


async def test_metric_capture_writes_llm_row(tmp_path):
    """An LLM component's metric becomes a request row through the sink."""
    storage = StorageService(str(tmp_path / "cap.db"))
    sink = LocalSqliteSink(storage)
    cost_tracker = CostTracker(sink)
    llm = _FakeEmitter(model="gpt-4o-mini", provider="openai")
    session = _FakeSession(llm=llm)

    capture = MetricCapture(
        cost_tracker=cost_tracker,
        sink=sink,
        project="fleet",
        agent_id="agent-3",
        session_id="vg-test",
    )
    capture.bind(session)

    llm.emit("metrics_collected", _LLMMetric())
    await capture.drain()

    rows = await storage.get_recent_requests(limit=10)
    assert len(rows) == 1
    row = rows[0]
    assert row["modality"] == "llm"
    assert row["model_id"] == "openai/gpt-4o-mini"
    assert row["provider"] == "openai"
    assert row["input_units"] == 1000.0
    assert row["output_units"] == 500.0
    assert row["cached_input_units"] == 200.0
    assert row["agent_id"] == "agent-3"
    assert row["session_id"] == "vg-test"


async def test_metric_capture_handles_three_modalities(tmp_path):
    """STT, LLM, and TTS components each produce a correctly-typed row."""
    storage = StorageService(str(tmp_path / "cap3.db"))
    sink = LocalSqliteSink(storage)
    cost_tracker = CostTracker(sink)
    stt = _FakeEmitter(model="nova-3", provider="deepgram")
    llm = _FakeEmitter(model="gpt-4o-mini", provider="openai")
    tts = _FakeEmitter(model="sonic-3", provider="cartesia")
    session = _FakeSession(stt=stt, llm=llm, tts=tts)

    capture = MetricCapture(
        cost_tracker=cost_tracker,
        sink=sink,
        project="fleet",
        agent_id="agent-3",
        session_id="vg-3",
    )
    capture.bind(session)

    stt.emit("metrics_collected", _STTMetric())
    llm.emit("metrics_collected", _LLMMetric())
    tts.emit("metrics_collected", _TTSMetric())
    await capture.drain()

    rows = await storage.get_recent_requests(limit=10)
    by_modality = {r["modality"]: r for r in rows}
    assert set(by_modality) == {"stt", "llm", "tts"}
    assert by_modality["stt"]["model_id"] == "deepgram/nova-3"
    assert by_modality["stt"]["input_units"] == 2.0
    assert by_modality["tts"]["model_id"] == "cartesia/sonic-3"
    assert by_modality["tts"]["input_units"] == 350.0


class _LLMErrorSource:
    """Source object whose type name marks the failing modality."""


class _ErrorEvent:
    def __init__(self, message: str, source: Any) -> None:
        self.error = message
        self.source = source


async def test_metric_capture_records_session_error(tmp_path):
    """A session ErrorEvent becomes an error-status request row."""
    storage = StorageService(str(tmp_path / "caperr.db"))
    sink = LocalSqliteSink(storage)
    cost_tracker = CostTracker(sink)
    session = _FakeSession(llm=_FakeEmitter(model="gpt-4o-mini", provider="openai"))

    capture = MetricCapture(
        cost_tracker=cost_tracker,
        sink=sink,
        project="fleet",
        agent_id="agent-e",
        session_id="vg-err",
    )
    capture.bind(session)

    session.emit("error", _ErrorEvent("upstream 500", _LLMErrorSource()))
    await capture.drain()

    rows = await storage.get_recent_requests(limit=10)
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "error"
    assert "upstream 500" in (row["error_message"] or "")
    assert row["modality"] == "llm"
    assert row["agent_id"] == "agent-e"


async def test_public_attach_captures_metric_through_injected_sink(tmp_path):
    """voicegateway.attach() binds capture and writes through the given sink."""
    import voicegateway

    storage = StorageService(str(tmp_path / "attach.db"))
    sink = LocalSqliteSink(storage)
    llm = _FakeEmitter(model="gpt-4o-mini", provider="openai")
    session = _FakeSession(llm=llm)

    sid = voicegateway.attach(session, project="fleet", agent_id="agent-x", sink=sink)
    assert sid is not None

    llm.emit("metrics_collected", _LLMMetric())
    await session._vg_capture.drain()

    rows = await storage.get_recent_requests(limit=10)
    assert len(rows) == 1
    assert rows[0]["agent_id"] == "agent-x"
    assert rows[0]["session_id"] == sid


def test_build_default_sink_uses_remote_when_collector_url_set():
    from voicegateway.inference.session.attach import _build_default_sink
    from voicegateway.services.sinks import RemoteCollectorSink

    sink = _build_default_sink("http://collector", "vk_x")
    assert isinstance(sink, RemoteCollectorSink)


def test_build_default_sink_uses_local_without_collector(tmp_path, monkeypatch):
    from voicegateway.inference.session.attach import _build_default_sink
    from voicegateway.services.sinks import LocalSqliteSink

    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "local.db"))
    sink = _build_default_sink(None, None)
    assert isinstance(sink, LocalSqliteSink)
