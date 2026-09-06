"""Representative native LiveKit-to-accounting integration checks."""

from __future__ import annotations

from typing import Any

from livekit.agents import llm, stt, tts
from livekit.agents.metrics import (
    LLMMetrics,
    RealtimeModelMetrics,
    STTMetrics,
    TTSMetrics,
)

from voicegateway import attach
from voicegateway.accounting.contracts import OwnershipMode, PricingBindingResponse
from voicegateway.accounting.outbox import AccountingOutbox
from voicegateway.models.request_model import RequestRecord
from voicegateway.telemetry import SpanContext, reset_trace_context, set_trace_context


class _NativeSTT(stt.STT):
    __module__ = "livekit.plugins.synthetic.stt"

    def __init__(self) -> None:
        super().__init__(
            capabilities=stt.STTCapabilities(streaming=True, interim_results=True)
        )
        self._model = "stt-model"

    @property
    def model(self) -> str:
        return "stt-model"

    async def _recognize_impl(self, *args: Any, **kwargs: Any):  # pragma: no cover
        raise NotImplementedError


class _NativeTTS(tts.TTS):
    __module__ = "livekit.plugins.synthetic.tts"

    def __init__(self) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=True),
            sample_rate=24_000,
            num_channels=1,
        )
        self._model = "tts-model"

    @property
    def model(self) -> str:
        return "tts-model"

    def synthesize(self, *args: Any, **kwargs: Any):  # pragma: no cover
        raise NotImplementedError


class _NativeLLM(llm.LLM):
    __module__ = "livekit.plugins.synthetic.llm"

    def __init__(self) -> None:
        super().__init__()
        self._model = "llm-model"

    @property
    def model(self) -> str:
        return "llm-model"

    def chat(self, *args: Any, **kwargs: Any):  # pragma: no cover
        raise NotImplementedError


class _NativeRealtime(llm.RealtimeModel):
    __module__ = "livekit.plugins.synthetic.realtime"

    def __init__(self) -> None:
        super().__init__(
            capabilities=llm.RealtimeCapabilities(
                message_truncation=True,
                turn_detection=True,
                user_transcription=True,
                auto_tool_reply_generation=True,
                audio_output=True,
                manual_function_calls=True,
            )
        )

    @property
    def model(self) -> str:
        return "realtime-model"

    @property
    def provider(self) -> str:
        return "synthetic"

    def session(self):  # pragma: no cover
        raise NotImplementedError

    async def aclose(self) -> None:  # pragma: no cover
        return None


class _Session:
    def __init__(
        self,
        *,
        stt_instance: Any = None,
        llm_instance: Any = None,
        tts_instance: Any = None,
        agent_llm: Any = None,
    ) -> None:
        self.stt = stt_instance
        self.llm = llm_instance
        self.tts = tts_instance
        self.current_agent = type("Agent", (), {"llm": agent_llm})()
        self._handlers: dict[str, list[Any]] = {}

    def on(self, event: str, handler: Any) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def emit(self, event: str, value: Any) -> None:
        for handler in self._handlers.get(event, []):
            handler(value)


class _RecordingSink:
    def __init__(self) -> None:
        self.records: list[RequestRecord] = []

    async def log_request(self, record: RequestRecord) -> None:
        self.records.append(record)

    async def log_turns(self, _rows) -> None:
        return None

    async def log_dead_air(self, _events) -> None:
        return None

    async def log_tool_calls(self, _rows) -> None:
        return None

    async def flush(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


class _Response:
    status_code = 200

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def json(self) -> dict[str, object]:
        return self._payload

    def raise_for_status(self) -> None:
        return None


class _Collector:
    def __init__(self, *, status_code: int = 200) -> None:
        self.status_code = status_code
        self.envelopes: list[dict[str, object]] = []

    async def post(self, _url: str, **kwargs: Any) -> _Response:
        batch = kwargs["json"]
        self.envelopes.extend(batch)
        response = _Response(
            {
                "receipts": [
                    {
                        "event_id": row["event_id"],
                        "outcome": "accepted",
                        "receipt_id": f"receipt:{row['event_id']}",
                        "code": "committed",
                    }
                    for row in batch
                ]
            }
        )
        response.status_code = self.status_code
        return response


class _CancelledIncompleteMetric:
    request_id = "llm-retry-2"
    timestamp = 1_800_000_004.0
    duration = 0.1
    ttft = None
    cancelled = True


def _binding(
    offering: str, mode: OwnershipMode = OwnershipMode.SDK
) -> PricingBindingResponse:
    return PricingBindingResponse(
        binding_id=f"binding:{offering}",
        project_id="default",
        component="conversation",
        offering=offering,
        selling_revision_id="selling-v1",
        ownership_mode=mode,
        prepared_at_ns=1,
    )


def _quantities(envelope: dict[str, object]) -> dict[str, dict[str, object]]:
    return {
        str(item["dimension"]): item
        for item in envelope["quantities"]  # type: ignore[union-attr]
    }


async def test_attach_captures_native_stt_tts_llm_and_realtime_without_duplicates(
    tmp_path,
) -> None:
    collector = _Collector()
    outbox = AccountingOutbox(
        tmp_path / "native.db", "http://collector.invalid", client=collector
    )
    sink = _RecordingSink()
    native_stt = _NativeSTT()
    native_tts = _NativeTTS()
    native_llm = _NativeLLM()
    session = _Session(
        stt_instance=native_stt,
        llm_instance=native_llm,
        tts_instance=native_tts,
    )

    attach(
        session,
        sink=sink,
        transcript=False,
        turns=False,
        dead_air=False,
        accounting_outbox=outbox,
        accounting_binding={
            model_id: _binding(model_id)
            for model_id in (
                "synthetic/stt-model",
                "synthetic/llm-model",
                "synthetic/tts-model",
                "synthetic/realtime-model",
            )
        },
        accounting_producer_id="native-worker",
    )
    # Registration happens synchronously in attach(), before any provider call.
    assert len(native_stt._events["metrics_collected"]) == 1
    assert len(native_tts._events["metrics_collected"]) == 1
    assert len(native_llm._events["metrics_collected"]) == 1

    native_stt.emit(
        "metrics_collected",
        STTMetrics(
            label="stt",
            request_id="stt-1",
            timestamp=1_800_000_000.0,
            duration=0.4,
            audio_duration=2.25,
            streamed=True,
        ),
    )
    trace_token = set_trace_context(
        SpanContext(trace_id="1" * 32, span_id="2" * 16, trace_flags=1)
    )
    try:
        native_llm.emit(
            "metrics_collected",
            LLMMetrics(
                label="llm",
                request_id="llm-1",
                timestamp=1_800_000_001.0,
                duration=0.8,
                ttft=0.1,
                cancelled=False,
                completion_tokens=7,
                prompt_tokens=20,
                prompt_cached_tokens=3,
                total_tokens=27,
                tokens_per_second=10,
            ),
        )
    finally:
        reset_trace_context(trace_token)
    for segment, timestamp in (
        ("segment-a", 1_800_000_002.0),
        ("segment-b", 1_800_000_003.0),
    ):
        metric = TTSMetrics(
            label="tts",
            request_id="tts-stream-1",
            segment_id=segment,
            timestamp=timestamp,
            ttfb=0.05,
            duration=0.2,
            audio_duration=0.1,
            cancelled=False,
            characters_count=11,
            streamed=True,
        )
        native_tts.emit("metrics_collected", metric)
        if segment == "segment-a":
            native_tts.emit("metrics_collected", metric)  # duplicate SDK event
    native_llm.emit("metrics_collected", _CancelledIncompleteMetric())
    await session._vg_capture.drain()

    realtime = _NativeRealtime()
    realtime_session = _Session(agent_llm=realtime)
    attach(
        realtime_session,
        sink=sink,
        transcript=False,
        turns=False,
        dead_air=False,
        accounting_outbox=outbox,
        accounting_binding={
            "synthetic/realtime-model": _binding("synthetic/realtime-model")
        },
        accounting_producer_id="native-worker",
    )
    cached_details = RealtimeModelMetrics.CachedTokenDetails(
        text_tokens=2, audio_tokens=3
    )
    realtime_session.emit(
        "metrics_collected",
        RealtimeModelMetrics(
            request_id="realtime-1",
            timestamp=1_800_000_005.0,
            duration=1.0,
            input_tokens=35,
            output_tokens=17,
            total_tokens=52,
            input_token_details=RealtimeModelMetrics.InputTokenDetails(
                text_tokens=20,
                audio_tokens=15,
                cached_tokens=5,
                cached_tokens_details=cached_details,
            ),
            output_token_details=RealtimeModelMetrics.OutputTokenDetails(
                text_tokens=7, audio_tokens=10
            ),
        ),
    )
    await realtime_session._vg_capture.drain()
    await outbox.flush_memory()
    result = await outbox.drain()
    assert result == {"accepted": 6, "duplicate": 0, "rejected": 0, "retryable": 0}

    # Duplicate TTS delivery had the same deterministic event ID; the two real
    # stream segments share provider correlation but remain distinct charges.
    assert len(collector.envelopes) == 6
    tts_rows = [row for row in collector.envelopes if row["modality"] == "tts"]
    assert len(tts_rows) == 2
    assert len({row["event_id"] for row in tts_rows}) == 2
    assert {
        row["model_id"]: row["pricing_binding_id"] for row in collector.envelopes
    } == {
        model_id: f"binding:{model_id}"
        for model_id in {
            "synthetic/stt-model",
            "synthetic/llm-model",
            "synthetic/tts-model",
            "synthetic/realtime-model",
        }
    }
    observed_tts = [record for record in sink.records if record.modality == "tts"]
    assert {record.metadata["provider_request_id"] for record in observed_tts} == {
        "tts-stream-1"
    }
    assert {record.metadata["provider_segment_id"] for record in observed_tts} == {
        "segment-a",
        "segment-b",
    }

    cancelled = next(
        row
        for row in collector.envelopes
        if _quantities(row)["text_input"]["status"] == "missing"
    )
    assert _quantities(cancelled)["text_output"] == {
        "dimension": "text_output",
        "value": None,
        "status": "missing",
    }
    realtime_row = next(
        row
        for row in collector.envelopes
        if row["model_id"] == "synthetic/realtime-model"
    )
    realtime_quantities = _quantities(realtime_row)
    assert realtime_quantities["realtime_audio_input"]["value"] == "15"
    assert realtime_quantities["realtime_audio_output"]["value"] == "10"
    assert realtime_quantities["realtime_audio_cache"]["value"] == "3"
    assert realtime_quantities["cache_read"]["value"] == "2"
    await outbox.aclose()


async def test_attach_external_ownership_and_collector_outage_are_explicit(
    tmp_path,
) -> None:
    native_llm = _NativeLLM()
    external_collector = _Collector()
    external_outbox = AccountingOutbox(
        tmp_path / "external.db",
        "http://collector.invalid",
        client=external_collector,
    )
    external_session = _Session(llm_instance=native_llm)
    attach(
        external_session,
        sink=_RecordingSink(),
        transcript=False,
        turns=False,
        dead_air=False,
        accounting_outbox=external_outbox,
        accounting_binding=_binding("synthetic/llm-model", OwnershipMode.EXTERNAL),
        accounting_producer_id="native-worker",
    )
    native_llm.emit(
        "metrics_collected",
        LLMMetrics(
            label="llm",
            request_id="external-1",
            timestamp=1_800_000_010.0,
            duration=0.2,
            ttft=0.1,
            cancelled=False,
            completion_tokens=1,
            prompt_tokens=1,
            prompt_cached_tokens=0,
            total_tokens=2,
            tokens_per_second=5,
        ),
    )
    await external_session._vg_capture.drain()
    await external_outbox.flush_memory()
    assert (await external_outbox.health())["pending"] == 0
    assert external_collector.envelopes == []
    await external_outbox.aclose()

    outage_collector = _Collector(status_code=503)
    outage_outbox = AccountingOutbox(
        tmp_path / "outage.db",
        "http://collector.invalid",
        client=outage_collector,
    )
    outage_llm = _NativeLLM()
    outage_session = _Session(llm_instance=outage_llm)
    attach(
        outage_session,
        sink=_RecordingSink(),
        transcript=False,
        turns=False,
        dead_air=False,
        accounting_outbox=outage_outbox,
        accounting_binding=_binding("synthetic/llm-model"),
        accounting_producer_id="native-worker",
    )
    outage_llm.emit(
        "metrics_collected",
        LLMMetrics(
            label="llm",
            request_id="outage-1",
            timestamp=1_800_000_011.0,
            duration=0.2,
            ttft=0.1,
            cancelled=False,
            completion_tokens=1,
            prompt_tokens=1,
            prompt_cached_tokens=0,
            total_tokens=2,
            tokens_per_second=5,
        ),
    )
    await outage_session._vg_capture.drain()
    await outage_outbox.flush_memory()
    assert (await outage_outbox.drain())["retryable"] == 1
    health = await outage_outbox.health()
    assert health["pending"] == 1
    assert health["failed_delivery"] == 1
    await outage_outbox.aclose()
