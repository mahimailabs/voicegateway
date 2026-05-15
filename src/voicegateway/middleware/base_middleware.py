"""Shared Protocols and exception base for the middleware package.

The runtime-checkable Protocols here describe the three lifecycle shapes
shared across the package's component classes. Existing middleware
classes subclass the matching Protocol nominally so callers can
``isinstance``-check against the contract and so IDE navigation jumps
from the class declaration to the Protocol definition.

The ``InstrumentationMixin`` for LiveKit STT/LLM/TTS subclassing arrives
in a follow-up commit (promoted from the private ``_InstrumentedBase``
mixin in ``instrumented_provider_middleware.py``).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


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


__all__ = [
    "AsyncWorker",
    "MiddlewareError",
    "PerSessionWatcher",
    "SessionScopedComponent",
]
