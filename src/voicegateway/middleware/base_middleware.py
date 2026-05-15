"""Shared Protocols, mixins, and exception base for the middleware package.

The runtime-checkable Protocols here describe the three lifecycle shapes
shared across the package's component classes. Existing middleware
classes subclass the matching Protocol nominally so callers can
``isinstance``-check against the contract and so IDE navigation jumps
from the class declaration to the Protocol definition.

``InstrumentationMixin`` is the mixin shared by ``InstrumentedSTT``,
``InstrumentedLLM``, and ``InstrumentedTTS``. Subclasses must also
extend the matching LiveKit base class (``livekit.agents.stt.STT``, etc.).
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from voicegateway.middleware.cost_tracker_middleware import CostTracker
    from voicegateway.services.storage_service import StorageService

logger = logging.getLogger(__name__)


class MiddlewareError(Exception):
    """Base for every error raised by the middleware package."""


@runtime_checkable
class SessionScopedComponent(Protocol):
    """Component that maintains per-session state and needs cleanup.

    Implementations: ``TurnTracker``, ``ReplayCapture``, ``StateSnapshotter``.
    Components that buffer rows/events also implement an async
    ``flush_session(session_id) -> int`` method; the Protocol does not
    declare it because not every implementation needs flushing.
    """

    async def close_session(self, session_id: str) -> int | None: ...

    def active_sessions(self) -> list[str]: ...


@runtime_checkable
class PerSessionWatcher(Protocol):
    """Per-session async watcher: one asyncio task per session.

    Implementations: ``DeadAirDetector``.
    """

    async def start(self, session_id: str) -> None: ...

    async def stop(self, session_id: str) -> None: ...

    def active_sessions(self) -> list[str]: ...


@runtime_checkable
class AsyncWorker(Protocol):
    """Long-running background worker bound to a single asyncio task.

    Implementations: ``LatencyObservationsWorker``.
    """

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


class InstrumentationMixin:
    """Cost/latency state shared by the LK STT/LLM/TTS wrappers.

    Subclasses must also extend the matching LiveKit base class
    (``livekit.agents.stt.STT`` for STT, etc.).
    """

    _modality: str = ""
    _wrapped: Any
    _model_id: str
    _provider: str
    project: str
    _cost_tracker: CostTracker
    _storage: StorageService | None
    _start_time: float
    _first_byte_time: float | None
    _logged: bool

    def _init_instrumentation(
        self,
        *,
        wrapped: Any,
        model_id: str,
        provider: str,
        project: str,
        cost_tracker: CostTracker,
        storage: StorageService | None,
    ) -> None:
        self._wrapped = wrapped
        self._model_id = model_id
        self._provider = provider
        self.project = project
        self._cost_tracker = cost_tracker
        self._storage = storage
        self._start_time = time.perf_counter()
        self._first_byte_time = None
        self._logged = False

        wrapped.on("metrics_collected", self._forward_metrics)
        wrapped.on("error", self._forward_error)

    def _forward_metrics(self, *args: Any, **kwargs: Any) -> None:
        self.emit("metrics_collected", *args, **kwargs)

    def _forward_error(self, *args: Any, **kwargs: Any) -> None:
        self.emit("error", *args, **kwargs)

    def _mark_first_byte(self) -> None:
        """Record the time of the first byte/token/audio frame."""
        if self._first_byte_time is None:
            self._first_byte_time = time.perf_counter()

    async def _log_request(
        self,
        input_units: float = 0.0,
        output_units: float = 0.0,
        status: str = "success",
        error_message: str | None = None,
    ) -> None:
        """Log the request to storage. Idempotent: subsequent calls no-op."""
        if self._logged:
            return
        self._logged = True

        now = time.perf_counter()
        total_ms = (now - self._start_time) * 1000
        ttfb_ms = (
            (self._first_byte_time - self._start_time) * 1000
            if self._first_byte_time is not None
            else total_ms
        )

        # Lazy: middleware <-> inference cycle. The session context
        # module imports nothing from middleware, so this is one-way,
        # but hoisting would still drag inference into base_middleware
        # at import time.
        from voicegateway.inference.session.context import get_session_id

        record = self._cost_tracker.create_record(
            model_id=self._model_id,
            modality=self._modality,
            provider=self._provider,
            project=self.project,
            input_units=input_units,
            output_units=output_units,
            ttfb_ms=ttfb_ms,
            total_latency_ms=total_ms,
            status=status,
            error_message=error_message,
            session_id=get_session_id(),
        )

        if self._storage is not None:
            try:
                await self._storage.log_request(record)
            except Exception:
                logger.warning("Failed to log request record", exc_info=True)

        await self._cost_tracker.notify_spend(record)

    def __getattr__(self, name: str) -> Any:
        # Only fires when normal attribute lookup fails. Provider-specific
        # helpers that aren't on the LK base class (e.g., a Cartesia-only
        # ``set_voice``) still flow through to the wrapped instance.
        wrapped = object.__getattribute__(self, "_wrapped")
        return getattr(wrapped, name)


__all__ = [
    "AsyncWorker",
    "InstrumentationMixin",
    "MiddlewareError",
    "PerSessionWatcher",
    "SessionScopedComponent",
]
