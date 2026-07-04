"""Per-component metric capture for ``voicegateway.attach()``.

Subscribes to the NON-DEPRECATED per-component ``metrics_collected`` events on
an ``AgentSession``'s stt/llm/tts components, maps each LiveKit metric to a
``RequestRecord``, and writes it through a ``Sink``. This works for ANY plugin,
because native LiveKit plugins (and ``livekit.agents.inference``) emit these
per-component events regardless of how they were constructed.

We deliberately avoid the session-level ``metrics_collected`` event, which is
deprecated in livekit-agents 1.5+. Unit conventions mirror
``InstrumentationMixin._extract_units``: LLM passes raw tokens, STT passes
audio MINUTES (the cost path multiplies back by 60 to recover seconds), TTS
passes characters.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from voicegateway.models.request_model import RequestRecord

if TYPE_CHECKING:
    from collections.abc import Iterator

    from voicegateway.middleware.cost_tracker_middleware import CostTracker
    from voicegateway.services.sinks import Sink

logger = logging.getLogger(__name__)

_MODALITIES: tuple[str, ...] = ("stt", "llm", "tts")

# Below this unit delta, a reconcile correction is not worth a row.
_RECONCILE_EPSILON = 1e-9


def _ttfb_ms(metric: object) -> float | None:
    """Return first-byte latency in ms from a metric's ttft/ttfb, if present."""
    value = getattr(metric, "ttft", None)
    if value is None:
        value = getattr(metric, "ttfb", None)
    # ``is not None`` so a genuine 0.0 latency records as 0.0, not None.
    return float(value) * 1000.0 if value is not None else None


def units_from_metric(
    metric: object, modality: str
) -> tuple[float, float, float, float | None]:
    """Return ``(input_units, output_units, cached_input_units, ttfb_ms)``.

    Defensive ``getattr(..., default) or default`` mirrors the middleware
    bridge: LK emits None for fields when a stream is cancelled before any
    tokens/audio land.
    """
    ttfb_ms = _ttfb_ms(metric)
    if modality == "llm":
        return (
            float(getattr(metric, "prompt_tokens", 0) or 0),
            float(getattr(metric, "completion_tokens", 0) or 0),
            float(getattr(metric, "prompt_cached_tokens", 0) or 0),
            ttfb_ms,
        )
    if modality == "stt":
        audio = float(getattr(metric, "audio_duration", 0.0) or 0.0)
        return (audio / 60.0, 0.0, 0.0, ttfb_ms)
    if modality == "tts":
        return (
            float(getattr(metric, "characters_count", 0) or 0),
            0.0,
            0.0,
            ttfb_ms,
        )
    return (0.0, 0.0, 0.0, ttfb_ms)


def _provider_name(component: object) -> str:
    """Best-effort provider id from a live plugin instance.

    Prefers the ``livekit.plugins.<provider>`` module segment; falls back to a
    ``provider``/``_provider`` attribute (useful for non-standard wrappers).
    """
    module = type(component).__module__ or ""
    parts = module.split(".")
    if "plugins" in parts:
        idx = parts.index("plugins")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    for attr in ("provider", "_provider"):
        value = getattr(component, attr, None)
        if isinstance(value, str) and value:
            return value
    return ""


def _model_name(component: object) -> str:
    """Best-effort model string from a live plugin instance."""
    for attr in ("model", "_model", "model_name"):
        value = getattr(component, attr, None)
        if isinstance(value, str) and value:
            return value
    opts = getattr(component, "_opts", None)
    if opts is not None:
        for attr in ("model", "model_name"):
            value = getattr(opts, attr, None)
            if isinstance(value, str) and value:
                return value
    return ""


def component_identity(component: object) -> tuple[str, str]:
    """Return ``(provider, model_id)`` for a plugin instance, best-effort.

    ``model_id`` is normalized to VG's ``provider/model`` form. Unknown
    components resolve to ``("unknown", "unknown")`` and still record (cost
    falls to 0 / unpriced, matching the catalog's existing behavior).
    """
    provider = _provider_name(component)
    model = _model_name(component)
    if provider and model:
        return provider, f"{provider}/{model}"
    if model:
        return provider or "unknown", model
    return provider or "unknown", "unknown"


def _error_modality(source: object) -> str:
    """Map an ErrorEvent.source to a modality string, best-effort."""
    name = type(source).__name__.lower() if source is not None else ""
    if "stt" in name:
        return "stt"
    if "tts" in name:
        return "tts"
    return "llm"


def _iter_components(session: object) -> Iterator[tuple[str, object]]:
    """Yield ``(modality, component)`` for each non-None stt/llm/tts slot."""
    for modality in _MODALITIES:
        component = getattr(session, modality, None)
        if component is not None:
            yield modality, component


def _usage_modality(entry: object) -> str:
    """Map an AgentSessionUsage.model_usage entry to a modality, best-effort."""
    name = type(entry).__name__.lower()
    if "stt" in name:
        return "stt"
    if "tts" in name:
        return "tts"
    if "llm" in name or "realtime" in name:
        return "llm"
    return ""


def _usage_units(entry: object, modality: str) -> tuple[float, float, float]:
    """Cumulative ``(input, output, cached)`` from a usage entry, VG units.

    STT seconds are converted to minutes to match the per-call rows. Field
    names are read defensively across the variants LiveKit may expose.
    """
    if modality == "llm":
        return (
            float(
                getattr(entry, "prompt_tokens", None)
                or getattr(entry, "input_tokens", 0)
                or 0
            ),
            float(
                getattr(entry, "completion_tokens", None)
                or getattr(entry, "output_tokens", 0)
                or 0
            ),
            float(
                getattr(entry, "prompt_cached_tokens", None)
                or getattr(entry, "cache_read_tokens", 0)
                or 0
            ),
        )
    if modality == "stt":
        seconds = float(
            getattr(entry, "audio_duration", None) or getattr(entry, "duration", 0) or 0
        )
        return (seconds / 60.0, 0.0, 0.0)
    if modality == "tts":
        return (
            float(
                getattr(entry, "characters", None)
                or getattr(entry, "characters_count", 0)
                or 0
            ),
            0.0,
            0.0,
        )
    return (0.0, 0.0, 0.0)


class MetricCapture:
    """Binds per-component metric + error events to a Sink write.

    Holds strong refs to the scheduled write tasks so the event loop's weak
    refs do not GC a pending write mid-flight (the same hazard the inference
    middleware guards against). ``drain`` awaits in-flight writes; the
    ``attach`` close path calls it.
    """

    def __init__(
        self,
        *,
        cost_tracker: CostTracker,
        sink: Sink,
        project: str,
        agent_id: str | None,
        session_id: str | None,
        tenant_id: str | None = None,
        room: str | None = None,
    ) -> None:
        self._cost_tracker = cost_tracker
        self._sink = sink
        self._project = project
        self._agent_id = agent_id
        self._session_id = session_id
        self._tenant_id = tenant_id
        self._room = room
        self._pending: set[asyncio.Task[None]] = set()
        # Per-(provider, model_id) running tally of captured units, so the
        # close-time reconcile can diff against cumulative session.usage.
        self._recorded: dict[tuple[str, str], dict[str, float]] = {}

    def bind(self, session: object) -> None:
        """Subscribe to every component's metrics + the session error event."""
        for modality, component in _iter_components(session):
            provider, model_id = component_identity(component)
            component.on(  # type: ignore[attr-defined]
                "metrics_collected",
                self._make_metric_handler(modality, provider, model_id),
            )
        on = getattr(session, "on", None)
        if callable(on):
            on("error", self._on_error)
            on("metrics_collected", self._on_session_metric)

    def _make_metric_handler(self, modality: str, provider: str, model_id: str) -> Any:
        def handler(metric: object, *_args: Any, **_kwargs: Any) -> None:
            input_units, output_units, cached, ttfb_ms = units_from_metric(
                metric, modality
            )
            status = (
                "cancelled" if bool(getattr(metric, "cancelled", False)) else "success"
            )
            record = self._cost_tracker.create_record(
                model_id=model_id,
                modality=modality,
                provider=provider,
                project=self._project,
                input_units=input_units,
                output_units=output_units,
                cached_input_units=cached,
                ttfb_ms=ttfb_ms,
                status=status,
                session_id=self._session_id,
                agent_id=self._agent_id,
            )
            self._stamp_context(record)
            tally = self._recorded.setdefault(
                (provider, model_id), {"input": 0.0, "output": 0.0, "cached": 0.0}
            )
            tally["input"] += input_units
            tally["output"] += output_units
            tally["cached"] += cached
            self._schedule(self._sink.log_request(record))

        return handler

    def _on_error(self, event: object, *_args: Any, **_kwargs: Any) -> None:
        source = getattr(event, "source", None)
        error = getattr(event, "error", event)
        record = RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=time.time(),
            modality=_error_modality(source),
            model_id="",
            provider="",
            project=self._project,
            status="error",
            error_message=str(error),
            session_id=self._session_id,
            agent_id=self._agent_id,
        )
        self._stamp_context(record)
        self._schedule(self._sink.log_request(record))

    def _on_session_metric(self, metric: object, *_a: Any, **_k: Any) -> None:
        metric = getattr(metric, "metrics", metric)
        eou = getattr(metric, "end_of_utterance_delay", None)
        if eou is None:
            return  # not an EOU metric; per-component metrics are handled elsewhere
        record = RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=time.time(),
            modality="eou",
            model_id="",
            provider="",
            project=self._project,
            status="success",
            session_id=self._session_id,
            agent_id=self._agent_id,
        )
        record.metadata = {
            "eou": {
                "end_of_utterance_delay": float(eou),
                "transcription_delay": float(
                    getattr(metric, "transcription_delay", 0.0) or 0.0
                ),
            }
        }
        self._stamp_context(record)
        self._schedule(self._sink.log_request(record))

    def _stamp_context(self, record: RequestRecord) -> None:
        """Carry the attach() ``tenant_id`` and probe ``room`` on ``metadata``.

        Both ride in ``metadata`` because ``RequestRecord`` has no column for
        them and the remote collector serializes only ``RequestRecord`` fields.
        ``tenant_id`` is how a per-call tenant survives the wire (the cloud
        stamps the top-level tenant from the ingest key), so an embedder that
        fans many sub-tenants through one ingest key can separate them
        downstream. ``room`` is how ``voicegw livekit latency`` correlates a
        throwaway probe room back to the STT/LLM/TTS + turn-detection split this
        agent captured. A local sink already gets the tenant first-class from the
        ``set_tenant`` ContextVar, so tenant is additive there, not a
        replacement.
        """
        extra: dict[str, Any] = {}
        if self._tenant_id:
            extra["tenant_id"] = self._tenant_id
        if self._room:
            extra["room"] = self._room
        if extra:
            record.metadata = {**record.metadata, **extra}

    def _schedule(self, coro: Any) -> None:
        try:
            task = asyncio.ensure_future(coro)
        except RuntimeError:
            # No running loop (sync test rig driving the binding directly).
            coro.close()
            return
        self._pending.add(task)
        task.add_done_callback(self._on_done)

    def _on_done(self, task: asyncio.Task[None]) -> None:
        self._pending.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning("attach: sink write failed", exc_info=exc)

    async def drain(self) -> None:
        """Await any in-flight sink writes scheduled by the bound handlers."""
        if not self._pending:
            return
        await asyncio.gather(*list(self._pending), return_exceptions=True)

    async def reconcile(self, session: object) -> None:
        """Diff cumulative ``session.usage`` against the per-call rows.

        The hybrid safety net: when the cumulative totals exceed what the
        per-call ``metrics_collected`` stream recorded (a dropped event, a
        realtime model that emits no per-component metrics, a handoff gap),
        emit one correction row per (provider, model) for the difference,
        tagged ``metadata.reconciled``. Awaited from the session close path.
        """
        usage = getattr(session, "usage", None)
        entries = getattr(usage, "model_usage", None) if usage is not None else None
        if not entries:
            return
        for entry in entries:
            modality = _usage_modality(entry)
            if modality not in _MODALITIES:
                continue
            provider = str(getattr(entry, "provider", "") or "")
            model = str(getattr(entry, "model", "") or "")
            model_id = (
                f"{provider}/{model}" if provider and model else (model or "unknown")
            )
            cum_in, cum_out, cum_cached = _usage_units(entry, modality)
            recorded = self._recorded.get(
                (provider, model_id), {"input": 0.0, "output": 0.0, "cached": 0.0}
            )
            d_in = cum_in - recorded["input"]
            d_out = cum_out - recorded["output"]
            d_cached = cum_cached - recorded["cached"]
            if (
                d_in <= _RECONCILE_EPSILON
                and d_out <= _RECONCILE_EPSILON
                and d_cached <= _RECONCILE_EPSILON
            ):
                continue
            record = self._cost_tracker.create_record(
                model_id=model_id,
                modality=modality,
                provider=provider,
                project=self._project,
                input_units=max(0.0, d_in),
                output_units=max(0.0, d_out),
                cached_input_units=max(0.0, d_cached),
                session_id=self._session_id,
                agent_id=self._agent_id,
            )
            record.metadata = {"reconciled": True}
            self._stamp_context(record)
            await self._sink.log_request(record)


__all__ = [
    "MetricCapture",
    "component_identity",
    "units_from_metric",
]
