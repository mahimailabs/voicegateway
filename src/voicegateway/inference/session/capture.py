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


def _ttfb_ms(metric: object) -> float | None:
    """Return first-byte latency in ms from a metric's ttft/ttfb, if present."""
    value = getattr(metric, "ttft", None)
    if value is None:
        value = getattr(metric, "ttfb", None)
    return float(value) * 1000.0 if value else None


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
    ) -> None:
        self._cost_tracker = cost_tracker
        self._sink = sink
        self._project = project
        self._agent_id = agent_id
        self._session_id = session_id
        self._tenant_id = tenant_id
        self._pending: set[asyncio.Task[None]] = set()

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
        self._schedule(self._sink.log_request(record))

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


__all__ = [
    "MetricCapture",
    "component_identity",
    "units_from_metric",
]
