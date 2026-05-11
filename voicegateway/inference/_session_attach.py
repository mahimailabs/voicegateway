"""``attach_session`` helper: opt-in escape hatch for non-standard worker patterns.

When the standard livekit-agents worker pattern is in use, the plugin-level
hooks on ``InstrumentedSTT`` and ``InstrumentedTTS`` are expected to capture
the VAD and audio-frame events the TurnTracker and DeadAirDetector need.
Foundry Open Question 1 flags this as the only architectural risk for v0.2.0
and points at the integration test in T17 as the validation gate.

For users on custom AgentSession subclasses, in-process agent harnesses, or
test rigs where the plugin hooks miss events, this module provides a manual
binding:

    from voicegateway import inference

    agent_session = AgentSession(...)
    inference.attach_session(agent_session)

The helper subscribes to the standard livekit-agents AgentSession events
(``user_started_speaking``, ``user_stopped_speaking``, ``agent_started_speaking``,
``agent_stopped_speaking``, ``close``) and forwards them into the process-level
TurnTracker, DeadAirDetector, and CostTracker via a small registry.

The component registry uses module-level globals because the v0.2.0 wiring
(T11 ProjectConfig knobs, the eventual Gateway-owned instance) is still
in flight. Callers can also pass components explicitly via the kwargs
overrides on ``attach_session`` for testability; T20 unit tests drive that
path with synthetic doubles.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from voicegateway.inference._session_context import (
    get_or_create_session_id,
)

if TYPE_CHECKING:
    from voicegateway.middleware.cost_tracker import CostTracker
    from voicegateway.middleware.dead_air_detector import DeadAirDetector
    from voicegateway.middleware.turn_tracker import TurnTracker

logger = logging.getLogger(__name__)


# Process-level component registry. The Gateway (or any other owner of the
# storage stack) calls ``register_components`` once on startup; subsequent
# ``attach_session`` calls read from here.
_active_turn_tracker: TurnTracker | None = None
_active_dead_air_detector: DeadAirDetector | None = None
_active_cost_tracker: CostTracker | None = None


def register_components(
    *,
    turn_tracker: TurnTracker | None = None,
    dead_air_detector: DeadAirDetector | None = None,
    cost_tracker: CostTracker | None = None,
) -> None:
    """Register the process-level metric-capture components.

    The Gateway sets these on startup. Multiple Gateways in the same
    process is not a supported configuration (matches the existing
    storage-layer contract).

    All three kwargs are optional and only update the registry slots
    that are explicitly passed. ``register_components()`` with no args
    is a no-op.
    """
    global _active_turn_tracker
    global _active_dead_air_detector
    global _active_cost_tracker
    if turn_tracker is not None:
        _active_turn_tracker = turn_tracker
    if dead_air_detector is not None:
        _active_dead_air_detector = dead_air_detector
    if cost_tracker is not None:
        _active_cost_tracker = cost_tracker


def reset_components() -> None:
    """Clear the registry. Tests use this between cases."""
    global _active_turn_tracker
    global _active_dead_air_detector
    global _active_cost_tracker
    _active_turn_tracker = None
    _active_dead_air_detector = None
    _active_cost_tracker = None


def _emit_user_started(session_id: str, tracker: TurnTracker) -> Any:
    """Return an event-handler coroutine that forwards user-started events."""

    async def _handler(*_args: Any, **_kwargs: Any) -> None:
        await tracker.on_user_started_speaking(session_id=session_id)

    return _handler


def _emit_user_stopped(session_id: str, tracker: TurnTracker) -> Any:
    async def _handler(*_args: Any, **_kwargs: Any) -> None:
        await tracker.on_user_stopped_speaking(session_id=session_id)

    return _handler


def _emit_agent_started(session_id: str, tracker: TurnTracker) -> Any:
    async def _handler(*_args: Any, **_kwargs: Any) -> None:
        await tracker.on_agent_audio_first_frame(session_id=session_id)

    return _handler


def _emit_agent_stopped(session_id: str, tracker: TurnTracker) -> Any:
    async def _handler(*_args: Any, **_kwargs: Any) -> None:
        await tracker.on_agent_audio_last_frame(session_id=session_id)

    return _handler


def _emit_close(
    session_id: str,
    tracker: TurnTracker,
    detector: DeadAirDetector | None,
    cost_tracker: CostTracker | None,
) -> Any:
    """Close handler: flush tracker, stop detector, finalize cost_tracker."""

    async def _handler(*_args: Any, **_kwargs: Any) -> None:
        try:
            await tracker.close_session(session_id)
        except Exception:
            logger.warning(
                "attach_session: tracker.close_session(%s) failed",
                session_id,
                exc_info=True,
            )
        if detector is not None:
            try:
                await detector.stop(session_id)
            except Exception:
                logger.warning(
                    "attach_session: detector.stop(%s) failed",
                    session_id,
                    exc_info=True,
                )
        if cost_tracker is not None:
            try:
                await cost_tracker.close_session(session_id)
            except Exception:
                logger.warning(
                    "attach_session: cost_tracker.close_session(%s) failed",
                    session_id,
                    exc_info=True,
                )

    return _handler


def attach_session(
    agent_session: Any,
    *,
    session_id: str | None = None,
    turn_tracker: TurnTracker | None = None,
    dead_air_detector: DeadAirDetector | None = None,
    cost_tracker: CostTracker | None = None,
) -> str:
    """Bind a LiveKit ``AgentSession`` to the v0.2.0 metric-capture pipeline.

    Subscribes to the AgentSession's standard event surface
    (``user_started_speaking``, ``user_stopped_speaking``,
    ``agent_started_speaking``, ``agent_stopped_speaking``, ``close``) and
    forwards each event into the process-level TurnTracker plus, on
    close, the DeadAirDetector and CostTracker. Starts the
    DeadAirDetector watcher task for the session id.

    The ``session_id`` defaults to whatever the
    ``voicegateway.inference`` ContextVar carries (creating a fresh
    ``vg-<uuid>`` if there is none). Pass an explicit id when the caller
    has its own correlation key.

    Returns the bound ``session_id`` so callers can echo it into their
    own logs.

    Component lookup order:

    1. Explicit kwargs (``turn_tracker``, ``dead_air_detector``,
       ``cost_tracker``). Used by T20 tests with synthetic doubles.
    2. Process-level registry populated by :func:`register_components`.
    3. If no TurnTracker is available, the call is a no-op (logs at
       warning level so the misconfiguration is visible).

    The AgentSession object is duck-typed: it must expose an
    ``on(event_name, handler)`` API. ``handler`` is registered for the
    five event names listed above; livekit-agents 1.x's
    ``AgentSession.on`` follows the standard ``EventEmitter`` contract
    so this works without an import on the SDK.
    """
    tracker = turn_tracker if turn_tracker is not None else _active_turn_tracker
    detector = (
        dead_air_detector
        if dead_air_detector is not None
        else _active_dead_air_detector
    )
    ct = cost_tracker if cost_tracker is not None else _active_cost_tracker

    sid = session_id if session_id is not None else get_or_create_session_id()

    if tracker is None:
        logger.warning(
            "attach_session(%s): no TurnTracker registered; "
            "events will be dropped. Call register_components(...) "
            "from the Gateway startup path first.",
            sid,
        )
        return sid

    agent_session.on("user_started_speaking", _emit_user_started(sid, tracker))
    agent_session.on("user_stopped_speaking", _emit_user_stopped(sid, tracker))
    agent_session.on("agent_started_speaking", _emit_agent_started(sid, tracker))
    agent_session.on("agent_stopped_speaking", _emit_agent_stopped(sid, tracker))
    agent_session.on("close", _emit_close(sid, tracker, detector, ct))

    if detector is not None:
        # Start the watcher synchronously by fire-and-forget; the
        # detector's start() is an async coroutine. Callers running
        # this from sync code want the watcher up immediately, so we
        # schedule it on the running event loop.
        import asyncio

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(detector.start(sid))
        except RuntimeError:
            # No running loop (sync test rig). Skip detector start;
            # the test must drive it explicitly.
            logger.debug(
                "attach_session(%s): no running event loop, "
                "skipping DeadAirDetector auto-start. Call "
                "detector.start(sid) explicitly.",
                sid,
            )

    return sid


__all__ = [
    "attach_session",
    "register_components",
    "reset_components",
]
