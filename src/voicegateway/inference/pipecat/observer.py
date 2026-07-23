"""Pipecat ``VoiceGatewayObserver``: the passive meter for the attach() path.

A Pipecat ``BaseObserver`` that watches frames flowing through a ``PipelineTask``
and meters them into VoiceGateway's framework-neutral core, exactly like the
LiveKit ``MetricCapture`` does for ``AgentSession`` events. It reuses the core
UNCHANGED: ``CostTracker.create_record`` for pricing (via voice-prices), the
``Sink`` for storage, and the session/tenant ContextVars for correlation.

Mapping (``on_push_frame`` sees ``FramePushed`` events; ``frame.data`` on a
``MetricsFrame`` is a list of ``MetricsData``):

- ``LLMUsageMetricsData`` (``.value`` is ``LLMTokenUsage``)
    -> modality=llm, input=prompt_tokens, output=completion_tokens,
       cached=cache_read_input_tokens.
- ``TTSUsageMetricsData`` (``.value`` is an int char count)
    -> modality=tts, input=chars.
- ``TTFBMetricsData`` (``.value`` seconds) -> ttfb_ms (first response, the ttft
    analog); ``ProcessingMetricsData`` (``.value`` seconds) -> total_latency_ms
    (total processing time, the LiveKit ``metric.duration`` analog). Pipecat
    exposes no connection-acquire metric, so a Pipecat row carries no network hop.
- STT has NO usage metric: accumulate ``AudioRawFrame`` bytes routed to each STT
  processor and derive seconds = bytes / (sample_rate * 2) (16-bit mono PCM),
  converted to minutes for input_units (the cost path multiplies back by 60).

Correlation boundary rule (the crux)
-------------------------------------
Pipecat emits TTFB and usage as SEPARATE ``MetricsData`` (often in separate
``MetricsFrame``s) from the SAME processor. We buffer per-processor and emit
exactly ONE ``RequestRecord`` per request:

- LLM/TTS: the USAGE metric is the request boundary. When a ``*UsageMetricsData``
  arrives for a processor we stitch in any TTFB buffered for that same processor
  (since the last flush), emit one record, and clear that processor's TTFB
  buffer. A lone TTFB (no usage yet) emits nothing; it waits for the usage frame.
  A second usage metric for the same processor is a second request.
- STT: there is no usage metric, so the boundary is the UTTERANCE. We accumulate
  audio bytes seen for the STT processor and flush one record on an utterance
  boundary frame (``UserStoppedSpeakingFrame`` / ``TranscriptionFrame``) or on
  pipeline end. Any TTFB buffered for the STT processor rides on that record.

A ``MetricsFrame`` may be re-pushed by pipeline wrappers (so the observer can see
the same frame twice with different ``source``s); we dedupe by frame ``id`` so a
single logical metric is metered exactly once.

Session finalize: on the pipeline end (``EndFrame`` / ``aclose()``) we drain any
pending STT audio into a final record, drain in-flight sink writes, and flush the
sink so a buffered remote sink pushes before shutdown.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

# This module is imported ONLY lazily (via the attach() dispatch and the
# ``voicegateway.Observer`` export), never by ``import voicegateway``. Importing
# pipecat at module load here is therefore safe and mirrors how the LiveKit
# ``guard_livekit`` module imports ``livekit`` at load. ``import voicegateway``
# purity is preserved by the lazy dispatch, not by dodging the import here.
from pipecat.observers.base_observer import BaseObserver

from voicegateway.inference.session.context import (
    current_guard_fallback_from,
    get_or_create_session_id,
    set_tenant,
)

if TYPE_CHECKING:
    from voicegateway.middleware.cost_tracker_middleware import CostTracker
    from voicegateway.models.request_model import RequestRecord
    from voicegateway.services.sinks import Sink

logger = logging.getLogger(__name__)

# 16-bit PCM: 2 bytes per sample per channel. STT audio duration derivation
# assumes mono 16-bit (the pipecat transport default); num_channels is honored
# when present so stereo audio does not double-count seconds.
_BYTES_PER_SAMPLE = 2


def _service_base_modality(processor: object) -> str | None:
    """Return ``"stt"``/``"llm"``/``"tts"`` from a processor's service base class.

    Walks the MRO's ``__module__`` strings to find the pipecat service base a
    processor derives from, without importing pipecat here (the module strings
    are already set on the loaded classes). Returns None when the processor is
    not a recognizable STT/LLM/TTS service (e.g. the Pipeline wrapper).
    """
    for base in type(processor).__mro__:
        module = getattr(base, "__module__", "") or ""
        name = getattr(base, "__name__", "")
        if module == "pipecat.services.stt_service" and name == "STTService":
            return "stt"
        if module == "pipecat.services.llm_service" and name == "LLMService":
            return "llm"
        if module == "pipecat.services.tts_service" and name == "TTSService":
            return "tts"
    return None


def _provider_from_module(processor: object) -> str:
    """Best-effort provider id from ``pipecat.services.<provider>.*``.

    Real pipecat provider services live under ``pipecat.services.<provider>``
    (e.g. ``pipecat.services.openai.llm``). We read the segment after
    ``services``. Falls back to the leading token of ``processor.name`` when the
    module is not under a provider package.
    """
    module = type(processor).__module__ or ""
    parts = module.split(".")
    if "services" in parts:
        idx = parts.index("services")
        if idx + 1 < len(parts):
            candidate = parts[idx + 1]
            # Skip the base modules (stt_service/llm_service/tts_service/etc.).
            if candidate and not candidate.endswith("_service"):
                return candidate
    name = getattr(processor, "name", "") or ""
    # Processor names look like "OpenAILLMService#0"; strip the "#N" suffix and
    # lowercase the leading alpha run as a weak provider hint.
    head = name.split("#", 1)[0]
    return head or ""


def _model_from_processor(processor: object) -> str:
    """Best-effort model string from a pipecat service's settings."""
    settings = getattr(processor, "_settings", None)
    if settings is not None:
        model = getattr(settings, "model", None)
        if isinstance(model, str) and model:
            return model
    for attr in ("model_name", "model"):
        value = getattr(processor, attr, None)
        if isinstance(value, str) and value:
            return value
    return ""


def pipecat_identity(processor: object) -> tuple[str, str]:
    """Return ``(provider, modality)`` for a pipecat service processor.

    Analog of the LiveKit ``component_identity()``: modality from the service
    base class (STTService/LLMService/TTSService), provider from the service
    module (``pipecat.services.<provider>.*``) or the processor name. Returns
    ``("", None-ish)`` semantics via the caller; here modality may be ``""`` when
    the processor is not a recognizable service.
    """
    modality = _service_base_modality(processor) or ""
    provider = _provider_from_module(processor)
    return provider, modality


def _model_id(provider: str, model: str) -> str:
    """Normalize to VG's ``provider/model`` form (matches the LiveKit path)."""
    if provider and model:
        return f"{provider}/{model}"
    if model:
        return model
    return "unknown"


class _PendingRequest:
    """Per-processor buffer stitching TTFB/TTFA with the usage that flushes it."""

    __slots__ = ("ttfb_ms", "total_latency_ms")

    def __init__(self) -> None:
        self.ttfb_ms: float | None = None
        self.total_latency_ms: float | None = None


class _STTAccumulator:
    """Per-STT-processor audio byte accumulator, flushed on utterance boundary."""

    __slots__ = ("bytes", "sample_rate", "num_channels")

    def __init__(self) -> None:
        self.bytes: int = 0
        self.sample_rate: int = 0
        self.num_channels: int = 1

    def add(self, audio: bytes, sample_rate: int, num_channels: int) -> None:
        self.bytes += len(audio)
        if sample_rate:
            self.sample_rate = sample_rate
        if num_channels:
            self.num_channels = num_channels

    def seconds(self) -> float:
        if not self.sample_rate:
            return 0.0
        frame_bytes = self.sample_rate * _BYTES_PER_SAMPLE * max(1, self.num_channels)
        return self.bytes / frame_bytes if frame_bytes else 0.0

    def reset(self) -> None:
        self.bytes = 0


def _lazy_frame_types() -> dict[str, Any]:
    """Import the pipecat frame/metric types this observer matches on.

    Imported lazily inside the observer (not at module top) is not required here
    because this module is itself only imported by the lazy attach dispatch /
    Observer export; but keeping the imports in one place documents the exact
    1.5.0 surface the observer is built against.
    """
    from pipecat.frames.frames import (
        EndFrame,
        InputAudioRawFrame,
        MetricsFrame,
        TranscriptionFrame,
        UserStoppedSpeakingFrame,
    )
    from pipecat.metrics.metrics import (
        LLMUsageMetricsData,
        TTFBMetricsData,
        TTSUsageMetricsData,
    )

    types: dict[str, Any] = {
        "MetricsFrame": MetricsFrame,
        "InputAudioRawFrame": InputAudioRawFrame,
        "TranscriptionFrame": TranscriptionFrame,
        "UserStoppedSpeakingFrame": UserStoppedSpeakingFrame,
        "EndFrame": EndFrame,
        "LLMUsageMetricsData": LLMUsageMetricsData,
        "TTSUsageMetricsData": TTSUsageMetricsData,
        "TTFBMetricsData": TTFBMetricsData,
    }
    # ProcessingMetricsData (total processing time, the Pipecat analog of
    # LiveKit's metric.duration) is optional across versions; when present it is
    # the total-latency source. Pipecat exposes no connection-acquire metric, so
    # a Pipecat row's network hop is legitimately absent (unlike LiveKit's
    # streaming STT/TTS acquire_time).
    try:
        from pipecat.metrics.metrics import ProcessingMetricsData

        types["ProcessingMetricsData"] = ProcessingMetricsData
    except Exception:  # noqa: BLE001 - older pipecat without ProcessingMetricsData
        types["ProcessingMetricsData"] = ()
    # AudioRawFrame is the base of InputAudioRawFrame; matching it catches output
    # audio too, but STT accumulates only input audio, so we key on the base and
    # let the STT-processor routing decide what counts.
    try:
        from pipecat.frames.frames import AudioRawFrame

        types["AudioRawFrame"] = AudioRawFrame
    except Exception:  # noqa: BLE001
        types["AudioRawFrame"] = InputAudioRawFrame
    return types


def _build_default_sink(collector_url: str | None, api_key: str | None) -> Sink:
    """Build the attach() sink from env/args (mirrors the LiveKit attach path)."""
    if collector_url:
        from voicegateway.services.sinks import RemoteCollectorSink

        return RemoteCollectorSink(collector_url, api_key)
    import os

    from voicegateway.inference.session.attach import DEFAULT_DB_PATH
    from voicegateway.services.sinks import LocalSqliteSink
    from voicegateway.services.storage_service import StorageService

    resolved_path = os.environ.get("VOICEGW_DB_PATH") or DEFAULT_DB_PATH
    return LocalSqliteSink(StorageService(resolved_path))


class VoiceGatewayObserver(BaseObserver):
    """A Pipecat ``BaseObserver`` that meters frames into VoiceGateway's core.

    Construct directly for ``PipelineTask(observers=[Observer(...)])`` or let
    ``attach(task)`` build and register one. It writes one ``RequestRecord`` per
    request through a ``Sink`` (local SQLite by default, or a remote collector).
    """

    def __init__(
        self,
        *,
        sink: Sink | None = None,
        cost_tracker: CostTracker | None = None,
        project: str = "default",
        agent_id: str | None = None,
        session_id: str | None = None,
        tenant_id: str | None = None,
        channel: str | None = None,
        collector_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        # BaseObserver.__init__ wires the pipecat event machinery.
        super().__init__()

        import os

        from voicegateway.middleware.cost_tracker_middleware import CostTracker

        resolved_collector = collector_url or os.environ.get("VOICEGW_COLLECTOR_URL")
        resolved_key = api_key or os.environ.get("VOICEGW_API_KEY")
        if sink is None:
            sink = _build_default_sink(resolved_collector, resolved_key)
        if tenant_id is not None:
            set_tenant(tenant_id)

        self._sink = sink
        self._cost_tracker = cost_tracker or CostTracker(sink)
        self._project = project
        self._agent_id = agent_id or _default_agent_id()
        self._session_id = session_id or get_or_create_session_id()
        self._tenant_id = tenant_id
        self._channel = channel
        self._types = _lazy_frame_types()

        # Per-processor stitch buffers and STT audio accumulators, keyed by the
        # processor's stable name (a MetricsData carries only the name string, so
        # the name is the correlation key that survives across frames).
        self._pending: dict[str, _PendingRequest] = {}
        self._stt_audio: dict[str, _STTAccumulator] = {}
        # STT processors registered for audio routing: name -> (provider, model).
        self._stt_identity: dict[str, tuple[str, str]] = {}
        # Frame ids already metered (re-push dedupe).
        self._seen_frames: set[Any] = set()
        self._closed = False

    # --- registration ------------------------------------------------------

    def register_stt(self, processor: object) -> None:
        """Register an STT service so its routed audio is accumulated.

        ``attach(task)`` discovers STT processors from the pipeline and registers
        them. Tests register a stub directly. Registration records the identity
        so the STT record carries the right provider/model even though STT emits
        no usage metric.
        """
        name = getattr(processor, "name", None)
        if not isinstance(name, str) or not name:
            return
        provider, modality = pipecat_identity(processor)
        model = _model_from_processor(processor)
        self._stt_identity[name] = (provider, model)
        self._stt_audio.setdefault(name, _STTAccumulator())

    # --- the observer hook -------------------------------------------------

    async def on_push_frame(self, data: Any) -> None:
        """Handle a ``FramePushed`` event (pipecat's ``BaseObserver`` hook)."""
        frame = data.frame
        source = data.source
        types = self._types

        if isinstance(frame, types["MetricsFrame"]):
            await self._on_metrics_frame(frame, source)
            return
        if isinstance(frame, types["AudioRawFrame"]):
            self._on_audio_frame(frame, source)
            return
        if isinstance(
            frame, (types["UserStoppedSpeakingFrame"], types["TranscriptionFrame"])
        ):
            await self._flush_stt_boundary(source)
            return
        if isinstance(frame, types["EndFrame"]):
            await self.aclose()
            return

    # --- metrics -----------------------------------------------------------

    async def _on_metrics_frame(self, frame: Any, source: object) -> None:
        # Dedupe re-pushed frames by id so one logical metric meters once.
        frame_id = getattr(frame, "id", None)
        if frame_id is not None:
            if frame_id in self._seen_frames:
                return
            self._seen_frames.add(frame_id)

        types = self._types
        data_list = getattr(frame, "data", None) or []
        # Two passes: first apply latency metrics into the per-processor buffer,
        # then flush usage metrics (so a TTFB in the same frame is stitched).
        for md in data_list:
            if isinstance(md, types["TTFBMetricsData"]):
                self._buffer_for(md).ttfb_ms = float(md.value) * 1000.0
            elif types["ProcessingMetricsData"] and isinstance(
                md, types["ProcessingMetricsData"]
            ):
                self._buffer_for(md).total_latency_ms = float(md.value) * 1000.0
        for md in data_list:
            if isinstance(md, types["LLMUsageMetricsData"]):
                await self._emit_llm(md, source)
            elif isinstance(md, types["TTSUsageMetricsData"]):
                await self._emit_tts(md, source)

    def _processor_name(self, md: Any) -> str:
        return getattr(md, "processor", "") or ""

    def _buffer_for(self, md: Any) -> _PendingRequest:
        return self._pending.setdefault(self._processor_name(md), _PendingRequest())

    def _identity_for_metric(
        self, md: Any, source: object, fallback_modality: str
    ) -> tuple[str, str]:
        """Resolve ``(provider, model_id)`` for a usage metric.

        Prefer the live ``source`` processor (its base class + module), which is
        the same object that pushed the frame; fall back to the metric's own
        ``model`` string. This mirrors the LiveKit ``component_identity``.
        """
        provider, modality = pipecat_identity(source)
        model = _model_from_processor(source) or (getattr(md, "model", "") or "")
        if not modality:
            modality = fallback_modality
        return provider, _model_id(provider, model)

    async def _emit_llm(self, md: Any, source: object) -> None:
        usage = md.value
        input_units = float(getattr(usage, "prompt_tokens", 0) or 0)
        output_units = float(getattr(usage, "completion_tokens", 0) or 0)
        cached = float(getattr(usage, "cache_read_input_tokens", 0) or 0)
        provider, model_id = self._identity_for_metric(md, source, "llm")
        await self._emit(
            modality="llm",
            provider=provider,
            model_id=model_id,
            input_units=input_units,
            output_units=output_units,
            cached_input_units=cached,
            processor_name=self._processor_name(md),
        )

    async def _emit_tts(self, md: Any, source: object) -> None:
        chars = float(md.value or 0)
        provider, model_id = self._identity_for_metric(md, source, "tts")
        await self._emit(
            modality="tts",
            provider=provider,
            model_id=model_id,
            input_units=chars,
            output_units=0.0,
            cached_input_units=0.0,
            processor_name=self._processor_name(md),
        )

    async def _emit(
        self,
        *,
        modality: str,
        provider: str,
        model_id: str,
        input_units: float,
        output_units: float,
        cached_input_units: float,
        processor_name: str,
        ttfb_ms: float | None = None,
        total_latency_ms: float | None = None,
    ) -> None:
        """Build ONE RequestRecord, stitch buffered latency, schedule the write."""
        pending = self._pending.pop(processor_name, None)
        if pending is not None:
            if ttfb_ms is None:
                ttfb_ms = pending.ttfb_ms
            if total_latency_ms is None:
                total_latency_ms = pending.total_latency_ms

        status = "success"
        fallback_from = current_guard_fallback_from()
        if fallback_from is not None and fallback_from != provider:
            status = "fallback"
        else:
            fallback_from = None

        record = self._cost_tracker.create_record(
            model_id=model_id,
            modality=modality,
            provider=provider,
            project=self._project,
            input_units=input_units,
            output_units=output_units,
            cached_input_units=cached_input_units,
            ttfb_ms=ttfb_ms,
            total_latency_ms=total_latency_ms,
            status=status,
            fallback_from=fallback_from,
            session_id=self._session_id,
            agent_id=self._agent_id,
        )
        self._stamp_context(record)
        await self._write(record)

    # --- STT audio ---------------------------------------------------------

    def _on_audio_frame(self, frame: Any, source: object) -> None:
        # Route audio to an STT processor. When the pushing source is itself an
        # STT service, attribute audio to it. Otherwise, if exactly one STT
        # processor is registered, attribute to it (the common single-STT case);
        # with multiple STT processors and a non-STT source, attribute to all
        # registered STT accumulators is ambiguous, so we skip (avoids
        # double-counting) and rely on the source-keyed path.
        target_name: str | None = None
        modality = _service_base_modality(source)
        source_name = getattr(source, "name", None)
        if modality == "stt" and isinstance(source_name, str):
            target_name = source_name
            if target_name not in self._stt_identity:
                self.register_stt(source)
        elif len(self._stt_audio) == 1:
            target_name = next(iter(self._stt_audio))
        if target_name is None:
            return
        acc = self._stt_audio.setdefault(target_name, _STTAccumulator())
        acc.add(
            getattr(frame, "audio", b"") or b"",
            int(getattr(frame, "sample_rate", 0) or 0),
            int(getattr(frame, "num_channels", 1) or 1),
        )

    async def _flush_stt_boundary(self, source: object) -> None:
        """Flush accumulated STT audio into one record per STT processor.

        The utterance boundary (user stopped speaking / a transcription) closes
        the current STT request. We flush every STT accumulator that has audio,
        because a boundary frame is not always pushed by the STT processor
        itself (VAD lives in the transport/turn analyzer).
        """
        for name, acc in self._stt_audio.items():
            if acc.bytes <= 0:
                continue
            await self._flush_one_stt(name, acc)

    async def _flush_one_stt(self, name: str, acc: _STTAccumulator) -> None:
        provider, model = self._stt_identity.get(name, ("", ""))
        seconds = acc.seconds()
        acc.reset()
        if seconds <= 0:
            return
        minutes = seconds / 60.0
        pending = self._pending.pop(name, None)
        ttfb_ms = pending.ttfb_ms if pending is not None else None
        record = self._cost_tracker.create_record(
            model_id=_model_id(provider, model),
            modality="stt",
            provider=provider,
            project=self._project,
            input_units=minutes,
            ttfb_ms=ttfb_ms,
            session_id=self._session_id,
            agent_id=self._agent_id,
        )
        self._stamp_context(record)
        await self._write(record)

    # --- context stamping (mirrors capture.py) -----------------------------

    def _stamp_context(self, record: RequestRecord) -> None:
        """Carry tenant + channel on ``metadata`` like the LiveKit capture path.

        ``tenant_id`` is how a per-call tenant survives the wire; ``channel``
        (telephony vs web) has no RequestRecord column and the dashboard reads it
        back per call. Absent keys mean the observer had nothing to stamp.
        """
        extra: dict[str, Any] = {}
        if self._tenant_id:
            extra["tenant_id"] = self._tenant_id
        if self._channel:
            extra["channel"] = self._channel
        if extra:
            record.metadata = {**record.metadata, **extra}

    # --- write plumbing ----------------------------------------------------

    async def _write(self, record: RequestRecord) -> None:
        """Await the sink write directly.

        ``on_push_frame`` is an async hook that pipecat awaits, so metering can
        write inline. A failed write must not break the pipeline, so it is logged
        and swallowed (the passive-meter contract: observability never affects the
        call path).
        """
        try:
            await self._sink.log_request(record)
        except Exception:  # noqa: BLE001 - observability must not break the pipeline
            logger.warning("pipecat observer: sink write failed", exc_info=True)

    async def drain(self) -> None:
        """No-op retained for API parity with the LiveKit capture path.

        Writes are awaited inline in ``_write`` (the async ``on_push_frame`` hook
        makes fire-and-forget unnecessary), so there is nothing to drain. Kept so
        callers can uniformly ``await observer.drain()``.
        """
        return

    async def aclose(self) -> None:
        """Finalize on pipeline end: drain STT audio, then flush the sink."""
        if self._closed:
            return
        self._closed = True
        # Drain any STT audio still buffered (no explicit utterance boundary).
        for name, acc in list(self._stt_audio.items()):
            if acc.bytes > 0:
                await self._flush_one_stt(name, acc)
        try:
            await self._sink.flush()
        except Exception:  # noqa: BLE001
            logger.warning("pipecat observer: sink flush failed", exc_info=True)


def _default_agent_id() -> str:
    """Resolve the agent label: explicit env > hostname > a constant."""
    import os
    import socket

    return os.environ.get("VOICEGW_AGENT_ID") or socket.gethostname() or "agent"


__all__ = ["VoiceGatewayObserver", "pipecat_identity"]
