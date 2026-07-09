"""Tests for the Pipecat ``VoiceGatewayObserver`` (P2 attach path).

These feed synthetic Pipecat frames (``MetricsFrame`` with LLM/TTS usage +
TTFB, and ``AudioRawFrame``s) to the observer and assert the emitted
``RequestRecord``s: modality, model, provider, units, cost (via voice-prices),
ttfb_ms, and that correlation yields exactly one record per request.

The observer reuses the framework-neutral core unchanged: ``CostTracker`` +
``Sink`` are built exactly like ``attach()`` builds them for LiveKit.

Stub services set ``__module__`` to ``pipecat.services.<provider>.<modality>``
so provider resolution (which reads the service module, exactly like a real
pipecat provider under ``pipecat.services.openai.llm`` etc.) is deterministic.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

pipecat = pytest.importorskip("pipecat")

from pipecat.frames.frames import (  # noqa: E402
    EndFrame,
    InputAudioRawFrame,
    MetricsFrame,
    TranscriptionFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import (  # noqa: E402
    LLMTokenUsage,
    LLMUsageMetricsData,
    TTFBMetricsData,
    TTSUsageMetricsData,
)
from pipecat.observers.base_observer import FramePushed  # noqa: E402
from pipecat.processors.frame_processor import FrameDirection  # noqa: E402
from pipecat.services.llm_service import LLMService  # noqa: E402
from pipecat.services.stt_service import STTService  # noqa: E402
from pipecat.services.tts_service import TTSService  # noqa: E402

from voicegateway.inference.pipecat.observer import (  # noqa: E402
    VoiceGatewayObserver,
    pipecat_identity,
)

# --- test doubles ----------------------------------------------------------


class _RecordingSink:
    """Captures every RequestRecord written, in order."""

    def __init__(self) -> None:
        self.records: list[Any] = []
        self.flushed = 0

    async def log_request(self, record: Any) -> None:
        self.records.append(record)

    async def flush(self) -> None:
        self.flushed += 1


class _StubLLM(LLMService):
    """A minimal concrete LLMService so identity resolves by base class."""

    # Emulate a real provider service living under pipecat.services.openai.llm.
    __module__ = "pipecat.services.openai.llm"

    def __init__(self, model: str = "gpt-4o") -> None:
        super().__init__()
        self._settings.model = model


class _StubTTS(TTSService):
    __module__ = "pipecat.services.cartesia.tts"

    def __init__(self, model: str = "sonic-2") -> None:
        super().__init__(sample_rate=24000)
        self._settings.model = model

    async def run_tts(self, text: str) -> Any:  # pragma: no cover - unused
        yield None


class _StubSTT(STTService):
    __module__ = "pipecat.services.deepgram.stt"

    def __init__(self, model: str = "nova-3") -> None:
        super().__init__(sample_rate=16000)
        self._settings.model = model

    async def run_stt(self, audio: bytes) -> Any:  # pragma: no cover - unused
        yield None


def _make_observer(**kwargs: Any) -> tuple[VoiceGatewayObserver, _RecordingSink]:
    sink = _RecordingSink()
    obs = VoiceGatewayObserver(sink=sink, project="proj", agent_id="agent-1", **kwargs)
    return obs, sink


def _pushed(source: Any, frame: Any) -> FramePushed:
    """Build a FramePushed event the observer's on_push_frame consumes."""
    return FramePushed(
        source=source,
        destination=None,
        frame=frame,
        direction=FrameDirection.DOWNSTREAM,
        timestamp=0,
    )


async def _feed(obs: VoiceGatewayObserver, source: Any, frame: Any) -> None:
    await obs.on_push_frame(_pushed(source, frame))


# --- identity --------------------------------------------------------------


def test_identity_llm_from_base_class_and_module() -> None:
    provider, modality = pipecat_identity(_StubLLM())
    assert modality == "llm"
    assert provider == "openai"


def test_identity_stt_tts_modalities_and_providers() -> None:
    assert pipecat_identity(_StubSTT()) == ("deepgram", "stt")
    assert pipecat_identity(_StubTTS()) == ("cartesia", "tts")


# --- LLM usage mapping -----------------------------------------------------


async def test_llm_usage_maps_units_and_costs() -> None:
    obs, sink = _make_observer()
    llm = _StubLLM("gpt-4o")
    usage = LLMTokenUsage(
        prompt_tokens=1000,
        completion_tokens=500,
        total_tokens=1500,
        cache_read_input_tokens=200,
    )
    md = LLMUsageMetricsData(processor=llm.name, model="gpt-4o", value=usage)
    await _feed(obs, llm, MetricsFrame(data=[md]))
    await obs.aclose()

    assert len(sink.records) == 1
    rec = sink.records[0]
    assert rec.modality == "llm"
    assert rec.provider == "openai"
    assert rec.model_id == "openai/gpt-4o"
    assert rec.input_units == 1000.0
    assert rec.output_units == 500.0
    assert rec.cached_input_units == 200.0
    # voice-prices priced gpt-4o at these units.
    assert rec.cost_usd == pytest.approx(0.00725, rel=1e-6)


# --- TTS usage mapping -----------------------------------------------------


async def test_tts_usage_maps_chars_and_costs() -> None:
    obs, sink = _make_observer()
    tts = _StubTTS("sonic-2")
    md = TTSUsageMetricsData(processor=tts.name, model="sonic-2", value=350)
    await _feed(obs, tts, MetricsFrame(data=[md]))
    await obs.aclose()

    assert len(sink.records) == 1
    rec = sink.records[0]
    assert rec.modality == "tts"
    assert rec.provider == "cartesia"
    assert rec.model_id == "cartesia/sonic-2"
    assert rec.input_units == 350.0
    assert rec.cost_usd == pytest.approx(0.014, rel=1e-6)


# --- correlation: TTFB stitches into ONE record per request ----------------


async def test_ttfb_and_usage_correlate_into_one_llm_record() -> None:
    """TTFB and usage arrive as SEPARATE frames; observer emits ONE record."""
    obs, sink = _make_observer()
    llm = _StubLLM("gpt-4o")
    # TTFB arrives first (as it does in Pipecat: first token), no usage yet.
    await _feed(
        obs, llm, MetricsFrame(data=[TTFBMetricsData(processor=llm.name, value=0.25)])
    )
    assert sink.records == []  # nothing emitted on a lone TTFB
    # Usage arrives next -> flush exactly one stitched record.
    usage = LLMTokenUsage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500)
    await _feed(
        obs,
        llm,
        MetricsFrame(data=[LLMUsageMetricsData(processor=llm.name, value=usage)]),
    )

    assert len(sink.records) == 1
    rec = sink.records[0]
    assert rec.ttfb_ms == pytest.approx(250.0)
    assert rec.input_units == 1000.0


async def test_ttfb_and_usage_same_frame_one_record() -> None:
    obs, sink = _make_observer()
    tts = _StubTTS("sonic-2")
    md_ttfb = TTFBMetricsData(processor=tts.name, value=0.1)
    md_usage = TTSUsageMetricsData(processor=tts.name, value=350)
    # Both in the same MetricsFrame -> still one record.
    await _feed(obs, tts, MetricsFrame(data=[md_ttfb, md_usage]))

    assert len(sink.records) == 1
    assert sink.records[0].ttfb_ms == pytest.approx(100.0)
    assert sink.records[0].input_units == 350.0


async def test_two_llm_requests_two_records() -> None:
    """A second usage frame for the same processor is a second request."""
    obs, sink = _make_observer()
    llm = _StubLLM("gpt-4o")
    usage = LLMTokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    await _feed(
        obs,
        llm,
        MetricsFrame(data=[LLMUsageMetricsData(processor=llm.name, value=usage)]),
    )
    await _feed(
        obs,
        llm,
        MetricsFrame(data=[LLMUsageMetricsData(processor=llm.name, value=usage)]),
    )
    assert len(sink.records) == 2


# --- STT audio derivation --------------------------------------------------


async def test_stt_audio_derivation_by_bytes() -> None:
    """seconds = bytes/(sample_rate*2); minutes = seconds/60; emit on boundary."""
    obs, sink = _make_observer()
    stt = _StubSTT("nova-3")
    obs.register_stt(stt)
    sr = 16000
    chunk = b"\x00\x01" * sr  # 1s of 16-bit mono = sr samples = sr*2 bytes
    for _ in range(120):
        await _feed(
            obs,
            stt,
            InputAudioRawFrame(audio=chunk, sample_rate=sr, num_channels=1),
        )
    assert sink.records == []  # no boundary crossed yet
    # Utterance boundary flushes the STT request.
    await _feed(obs, stt, UserStoppedSpeakingFrame())

    assert len(sink.records) == 1
    rec = sink.records[0]
    assert rec.modality == "stt"
    assert rec.provider == "deepgram"
    assert rec.model_id == "deepgram/nova-3"
    # 120 seconds -> 2.0 minutes.
    assert rec.input_units == pytest.approx(2.0)
    assert rec.cost_usd == pytest.approx(0.0096, rel=1e-6)


async def test_stt_transcription_frame_is_also_a_boundary() -> None:
    obs, sink = _make_observer()
    stt = _StubSTT("nova-3")
    obs.register_stt(stt)
    sr = 16000
    await _feed(
        obs,
        stt,
        InputAudioRawFrame(audio=b"\x00\x01" * sr, sample_rate=sr, num_channels=1),
    )
    await _feed(obs, stt, TranscriptionFrame("hi", "user", "0"))
    assert len(sink.records) == 1
    assert sink.records[0].modality == "stt"
    assert sink.records[0].input_units == pytest.approx(1.0 / 60.0)


# --- metadata stamping -----------------------------------------------------


async def test_metadata_stamps_tenant_channel_session() -> None:
    obs, sink = _make_observer(tenant_id="tenant-x", channel="telephony")
    llm = _StubLLM("gpt-4o")
    usage = LLMTokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    await _feed(
        obs,
        llm,
        MetricsFrame(data=[LLMUsageMetricsData(processor=llm.name, value=usage)]),
    )
    rec = sink.records[0]
    assert rec.metadata.get("tenant_id") == "tenant-x"
    assert rec.metadata.get("channel") == "telephony"
    assert rec.session_id is not None
    assert rec.agent_id == "agent-1"
    assert rec.project == "proj"


# --- session finalize ------------------------------------------------------


async def test_endframe_finalizes_and_flushes() -> None:
    obs, sink = _make_observer()
    stt = _StubSTT("nova-3")
    obs.register_stt(stt)
    sr = 16000
    await _feed(
        obs,
        stt,
        InputAudioRawFrame(audio=b"\x00\x01" * sr, sample_rate=sr, num_channels=1),
    )
    # No explicit utterance boundary; EndFrame must drain the pending STT audio.
    await _feed(obs, stt, EndFrame())
    assert sink.flushed >= 1
    assert len(sink.records) == 1
    assert sink.records[0].modality == "stt"


# --- ordering / event-loop safety -----------------------------------------


async def test_on_push_frame_is_awaitable_and_non_blocking() -> None:
    obs, sink = _make_observer()
    llm = _StubLLM("gpt-4o")
    usage = LLMTokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2)
    # Awaiting many frames sequentially records one per usage frame.
    for _ in range(5):
        await _feed(
            obs,
            llm,
            MetricsFrame(data=[LLMUsageMetricsData(processor=llm.name, value=usage)]),
        )
    assert len(sink.records) == 5
    await asyncio.sleep(0)
