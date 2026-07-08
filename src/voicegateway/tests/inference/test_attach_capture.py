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


def _stamp_capture(channel):
    """A MetricCapture built just to exercise _stamp_context (cost_tracker/sink unused there)."""
    return MetricCapture(
        cost_tracker=None,  # type: ignore[arg-type]
        sink=None,  # type: ignore[arg-type]
        project="fleet",
        agent_id="agent-3",
        session_id="vg-test",
        channel=channel,
    )


def _blank_record():
    from voicegateway.models.request_model import RequestRecord

    return RequestRecord(
        id="r",
        timestamp=0.0,
        modality="llm",
        model_id="openai/gpt-4o-mini",
        provider="openai",
    )


def test_stamp_context_adds_channel_when_set():
    """attach()'s telephony/web classification rides on each request's metadata."""
    record = _blank_record()
    _stamp_capture("telephony")._stamp_context(record)
    assert record.metadata["channel"] == "telephony"


def test_stamp_context_omits_channel_when_unknown():
    """No classification means the row carries no channel key, not a guess."""
    record = _blank_record()
    _stamp_capture(None)._stamp_context(record)
    assert "channel" not in record.metadata


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


# --- reconcile-at-close --------------------------------------------------


class _FakeLLMUsage:
    """Mirror of LiveKit LLMModelUsage: cumulative per (provider, model)."""

    def __init__(
        self,
        *,
        prompt_tokens: float,
        completion_tokens: float,
        prompt_cached_tokens: float = 200,
        provider: str = "openai",
        model: str = "gpt-4o-mini",
    ) -> None:
        self.provider = provider
        self.model = model
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.prompt_cached_tokens = prompt_cached_tokens


class _FakeUsage:
    def __init__(self, model_usage: list) -> None:
        self.model_usage = model_usage


class _FakeSessionWithUsage(_FakeSession):
    def __init__(self, usage: Any, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.usage = usage


async def test_reconcile_emits_correction_when_usage_exceeds_recorded(tmp_path):
    """Cumulative session.usage above the per-call rows yields a correction."""
    storage = StorageService(str(tmp_path / "recon.db"))
    sink = LocalSqliteSink(storage)
    cost_tracker = CostTracker(sink)
    llm = _FakeEmitter(model="gpt-4o-mini", provider="openai")
    usage = _FakeUsage(
        [
            _FakeLLMUsage(
                prompt_tokens=1500, completion_tokens=700, prompt_cached_tokens=200
            )
        ]
    )
    session = _FakeSessionWithUsage(usage, llm=llm)

    capture = MetricCapture(
        cost_tracker=cost_tracker,
        sink=sink,
        project="fleet",
        agent_id="agent-r",
        session_id="vg-rec",
    )
    capture.bind(session)

    # Per-call captured 1000 prompt / 500 completion / 200 cached.
    llm.emit("metrics_collected", _LLMMetric())
    await capture.drain()

    await capture.reconcile(session)

    rows = await storage.get_recent_requests(limit=10)
    reconciled = [
        r
        for r in rows
        if isinstance(r.get("metadata"), dict) and r["metadata"].get("reconciled")
    ]
    assert len(reconciled) == 1
    assert reconciled[0]["input_units"] == 500.0  # 1500 - 1000
    assert reconciled[0]["output_units"] == 200.0  # 700 - 500
    assert reconciled[0]["agent_id"] == "agent-r"


async def test_reconcile_no_correction_when_totals_match(tmp_path):
    """When session.usage matches the per-call rows, no correction is written."""
    storage = StorageService(str(tmp_path / "recon2.db"))
    sink = LocalSqliteSink(storage)
    cost_tracker = CostTracker(sink)
    llm = _FakeEmitter(model="gpt-4o-mini", provider="openai")
    usage = _FakeUsage(
        [
            _FakeLLMUsage(
                prompt_tokens=1000, completion_tokens=500, prompt_cached_tokens=200
            )
        ]
    )
    session = _FakeSessionWithUsage(usage, llm=llm)

    capture = MetricCapture(
        cost_tracker=cost_tracker,
        sink=sink,
        project="fleet",
        agent_id="agent-r",
        session_id="vg-rec2",
    )
    capture.bind(session)
    llm.emit("metrics_collected", _LLMMetric())
    await capture.drain()

    await capture.reconcile(session)

    rows = await storage.get_recent_requests(limit=10)
    reconciled = [
        r
        for r in rows
        if isinstance(r.get("metadata"), dict) and r["metadata"].get("reconciled")
    ]
    assert reconciled == []


class _ZeroTtftMetric:
    prompt_tokens = 10
    completion_tokens = 5
    prompt_cached_tokens = 0
    ttft = 0.0


def test_units_from_metric_zero_ttft_records_zero_not_none():
    """A genuine 0.0 first-byte latency records as 0.0, not None."""
    _inp, _out, _cached, ttfb_ms = units_from_metric(_ZeroTtftMetric(), "llm")
    assert ttfb_ms == 0.0


async def test_reconcile_emits_correction_for_cached_only_delta(tmp_path):
    """A cached-token-only divergence still produces a correction row."""
    storage = StorageService(str(tmp_path / "recon_cached.db"))
    sink = LocalSqliteSink(storage)
    cost_tracker = CostTracker(sink)
    llm = _FakeEmitter(model="gpt-4o-mini", provider="openai")
    # input/output match the per-call row (1000/500); only cached differs (500 vs 200).
    usage = _FakeUsage(
        [
            _FakeLLMUsage(
                prompt_tokens=1000, completion_tokens=500, prompt_cached_tokens=500
            )
        ]
    )
    session = _FakeSessionWithUsage(usage, llm=llm)

    capture = MetricCapture(
        cost_tracker=cost_tracker,
        sink=sink,
        project="fleet",
        agent_id="a-c",
        session_id="vg-c",
    )
    capture.bind(session)
    llm.emit("metrics_collected", _LLMMetric())  # records cached=200
    await capture.drain()

    await capture.reconcile(session)

    rows = await storage.get_recent_requests(limit=10)
    reconciled = [
        r
        for r in rows
        if isinstance(r.get("metadata"), dict) and r["metadata"].get("reconciled")
    ]
    assert len(reconciled) == 1
    assert reconciled[0]["cached_input_units"] == 300.0  # 500 - 200


class _FlushRecordingSink:
    def __init__(self) -> None:
        self.rows: list = []
        self.flushes = 0

    async def log_request(self, record: Any) -> None:
        self.rows.append(record)

    async def flush(self) -> None:
        self.flushes += 1

    async def aclose(self) -> None:
        pass


# --- per-call tenant on the wire -----------------------------------------


async def test_metric_capture_stamps_tenant_in_metadata():
    """attach(tenant_id=...) rides in ``record.metadata`` so it survives the
    remote wire: RequestRecord has no tenant field, and the cloud stamps the
    top-level tenant from the ingest key. A multi-tenant embedder (many
    sub-tenants behind one ingest key) separates them on this."""
    sink = _FlushRecordingSink()
    cost_tracker = CostTracker(sink)
    llm = _FakeEmitter(model="gpt-4o-mini", provider="openai")
    session = _FakeSession(llm=llm)

    capture = MetricCapture(
        cost_tracker=cost_tracker,
        sink=sink,
        project="realty-recall",
        agent_id="agent-t",
        session_id="vg-t",
        tenant_id="org_realtor_42",
    )
    capture.bind(session)
    llm.emit("metrics_collected", _LLMMetric())
    await capture.drain()

    assert len(sink.rows) == 1
    assert sink.rows[0].metadata.get("tenant_id") == "org_realtor_42"


async def test_metric_capture_omits_tenant_when_unset():
    """No tenant_id -> no ``tenant_id`` key added to metadata (stays empty)."""
    sink = _FlushRecordingSink()
    cost_tracker = CostTracker(sink)
    llm = _FakeEmitter(model="gpt-4o-mini", provider="openai")
    session = _FakeSession(llm=llm)

    capture = MetricCapture(
        cost_tracker=cost_tracker,
        sink=sink,
        project="fleet",
        agent_id="agent-n",
        session_id="vg-n",
    )
    capture.bind(session)
    llm.emit("metrics_collected", _LLMMetric())
    await capture.drain()

    assert "tenant_id" not in sink.rows[0].metadata


async def test_metric_capture_error_row_carries_tenant():
    """The error path also stamps the tenant so failures stay attributed."""
    sink = _FlushRecordingSink()
    cost_tracker = CostTracker(sink)
    session = _FakeSession(llm=_FakeEmitter(model="gpt-4o-mini", provider="openai"))

    capture = MetricCapture(
        cost_tracker=cost_tracker,
        sink=sink,
        project="realty-recall",
        agent_id="agent-te",
        session_id="vg-te",
        tenant_id="org_realtor_42",
    )
    capture.bind(session)
    session.emit("error", _ErrorEvent("boom", _LLMErrorSource()))
    await capture.drain()

    assert sink.rows[0].metadata.get("tenant_id") == "org_realtor_42"


async def test_reconcile_correction_carries_tenant():
    """A close-time reconcile correction keeps both markers: reconciled + tenant."""
    sink = _FlushRecordingSink()
    cost_tracker = CostTracker(sink)
    llm = _FakeEmitter(model="gpt-4o-mini", provider="openai")
    usage = _FakeUsage([_FakeLLMUsage(prompt_tokens=1500, completion_tokens=700)])
    session = _FakeSessionWithUsage(usage, llm=llm)

    capture = MetricCapture(
        cost_tracker=cost_tracker,
        sink=sink,
        project="realty-recall",
        agent_id="agent-tr",
        session_id="vg-tr",
        tenant_id="org_realtor_42",
    )
    capture.bind(session)
    llm.emit("metrics_collected", _LLMMetric())
    await capture.drain()
    await capture.reconcile(session)

    recon = [r for r in sink.rows if r.metadata.get("reconciled")]
    assert len(recon) == 1
    assert recon[0].metadata.get("tenant_id") == "org_realtor_42"


class _EOUMetric:
    end_of_utterance_delay = 0.12
    transcription_delay = 0.05


async def test_metric_capture_records_eou(tmp_path):
    from voicegateway.inference.session.capture import MetricCapture

    sink = _FlushRecordingSink()
    cost_tracker = CostTracker(sink)
    session = _FakeSession(llm=_FakeEmitter(model="gpt-4o-mini", provider="openai"))
    capture = MetricCapture(
        cost_tracker=cost_tracker,
        sink=sink,
        project="p",
        agent_id="a",
        session_id="s",
        tenant_id="t",
    )
    capture.bind(session)
    session.emit("metrics_collected", _EOUMetric())
    await capture.drain()
    eou = [r for r in sink.rows if r.metadata.get("eou")]
    assert len(eou) == 1
    assert eou[0].modality == "eou"
    assert eou[0].metadata["eou"]["end_of_utterance_delay"] == 0.12
    assert eou[0].metadata["tenant_id"] == "t"


class _MetricsCollectedEvent:
    def __init__(self, metrics):
        self.metrics = metrics


async def test_metric_capture_records_eou_wrapped():
    from voicegateway.inference.session.capture import MetricCapture

    sink = _FlushRecordingSink()
    cost_tracker = CostTracker(sink)
    session = _FakeSession(llm=_FakeEmitter(model="gpt-4o-mini", provider="openai"))
    capture = MetricCapture(
        cost_tracker=cost_tracker,
        sink=sink,
        project="p",
        agent_id="a",
        session_id="s",
        tenant_id="t",
    )
    capture.bind(session)
    session.emit("metrics_collected", _MetricsCollectedEvent(_EOUMetric()))
    await capture.drain()
    eou = [r for r in sink.rows if r.metadata.get("eou")]
    assert len(eou) == 1
    assert eou[0].modality == "eou"
    assert eou[0].metadata["eou"]["end_of_utterance_delay"] == 0.12
    assert eou[0].metadata["tenant_id"] == "t"


async def test_attach_close_flushes_sink(tmp_path):
    """The session close path drains writes AND flushes the sink (and the
    finalize task is strong-reffed: awaitable via session._vg_close_task)."""
    import voicegateway

    sink = _FlushRecordingSink()
    llm = _FakeEmitter(model="gpt-4o-mini", provider="openai")
    session = _FakeSessionWithUsage(_FakeUsage([]), llm=llm)

    voicegateway.attach(session, project="fleet", agent_id="agent-f", sink=sink)
    llm.emit("metrics_collected", _LLMMetric())
    session.emit("close")

    await session._vg_close_task

    assert sink.flushes >= 1
    assert len(sink.rows) >= 1


async def test_attach_marks_worker_busy_then_idle(monkeypatch):
    """With a registered worker, attach reflects a live session as busy and the
    close path drops it back to idle (the fleet-roster status signal)."""
    import voicegateway
    from voicegateway.fleet import worker

    monkeypatch.delenv("VOICEGW_COLLECTOR_URL", raising=False)
    worker._worker = None
    try:
        voicegateway.register_worker("realty")  # no collector -> local status only
        session = _FakeSessionWithUsage(
            _FakeUsage([]), llm=_FakeEmitter(model="gpt-4o-mini", provider="openai")
        )
        voicegateway.attach(session, project="fleet", sink=_FlushRecordingSink())
        assert worker._worker.active_sessions == 1
        assert worker._worker.status == "busy"

        session.emit("close")
        await session._vg_close_task
        assert worker._worker.active_sessions == 0
        assert worker._worker.status == "idle"
    finally:
        worker._worker = None


# --- probe room correlation ----------------------------------------------


async def test_metric_capture_stamps_room_in_metadata():
    """attach(room=...) rides in ``record.metadata`` so ``voicegw livekit
    latency`` can correlate a throwaway probe room back to this agent's split."""
    sink = _FlushRecordingSink()
    llm = _FakeEmitter(model="gpt-4o-mini", provider="openai")
    session = _FakeSession(llm=llm)

    capture = MetricCapture(
        cost_tracker=CostTracker(sink),
        sink=sink,
        project="p",
        agent_id="a",
        session_id="s",
        room="vg-probe-realty-abc123",
    )
    capture.bind(session)
    llm.emit("metrics_collected", _LLMMetric())
    await capture.drain()

    assert sink.rows[0].metadata.get("room") == "vg-probe-realty-abc123"


async def test_metric_capture_omits_room_when_unset():
    """No room -> no ``room`` key added (normal in-production capture)."""
    sink = _FlushRecordingSink()
    llm = _FakeEmitter(model="gpt-4o-mini", provider="openai")
    session = _FakeSession(llm=llm)

    capture = MetricCapture(
        cost_tracker=CostTracker(sink),
        sink=sink,
        project="p",
        agent_id="a",
        session_id="s",
    )
    capture.bind(session)
    llm.emit("metrics_collected", _LLMMetric())
    await capture.drain()

    assert "room" not in sink.rows[0].metadata


async def test_eou_row_carries_room():
    """The eou (turn-detection) row also carries the room, so the split's
    turn-detect number correlates with the same probe."""
    sink = _FlushRecordingSink()
    session = _FakeSession(llm=_FakeEmitter(model="gpt-4o-mini", provider="openai"))
    capture = MetricCapture(
        cost_tracker=CostTracker(sink),
        sink=sink,
        project="p",
        agent_id="a",
        session_id="s",
        room="vg-probe-x",
    )
    capture.bind(session)
    session.emit("metrics_collected", _EOUMetric())
    await capture.drain()

    eou = [r for r in sink.rows if r.metadata.get("eou")]
    assert eou[0].metadata["room"] == "vg-probe-x"
    assert eou[0].metadata["eou"]["end_of_utterance_delay"] == 0.12


async def test_attach_resolves_room_from_session_vg_room():
    """attach picks up an explicit ``session._vg_room`` and stamps it, without a
    LiveKit job context (the resolution's testable seam)."""
    import voicegateway

    sink = _FlushRecordingSink()
    llm = _FakeEmitter(model="gpt-4o-mini", provider="openai")
    session = _FakeSession(llm=llm)
    session._vg_room = "vg-probe-from-ctx"

    voicegateway.attach(session, project="fleet", agent_id="a", sink=sink)
    llm.emit("metrics_collected", _LLMMetric())
    await session._vg_capture.drain()

    assert sink.rows[0].metadata.get("room") == "vg-probe-from-ctx"


async def test_attach_explicit_room_overrides_session():
    """An explicit ``room=`` arg wins over ``session._vg_room``."""
    import voicegateway

    sink = _FlushRecordingSink()
    llm = _FakeEmitter(model="gpt-4o-mini", provider="openai")
    session = _FakeSession(llm=llm)
    session._vg_room = "from-session"

    voicegateway.attach(session, project="fleet", sink=sink, room="explicit-room")
    llm.emit("metrics_collected", _LLMMetric())
    await session._vg_capture.drain()

    assert sink.rows[0].metadata.get("room") == "explicit-room"
