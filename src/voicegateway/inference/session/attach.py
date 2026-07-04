"""``attach_session`` helper: opt-in escape hatch for non-standard worker patterns."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from voicegateway.inference.session.context import (
    RoutingDecisionTuple,
    get_or_create_session_id,
    set_guardrails_bypassed,
    set_routing_decision,
    set_tenant,
)

if TYPE_CHECKING:
    from voicegateway.middleware.cost_tracker_middleware import CostTracker
    from voicegateway.middleware.dead_air_detector_middleware import DeadAirDetector
    from voicegateway.middleware.turn_tracker_middleware import TurnTracker
    from voicegateway.services.sinks import Sink

DEFAULT_DB_PATH = "~/.config/voicegateway/voicegw.db"

logger = logging.getLogger(__name__)


_active_turn_tracker: TurnTracker | None = None
_active_dead_air_detector: DeadAirDetector | None = None
_active_cost_tracker: CostTracker | None = None


def register_components(
    *,
    turn_tracker: TurnTracker | None = None,
    dead_air_detector: DeadAirDetector | None = None,
    cost_tracker: CostTracker | None = None,
) -> None:
    """Register the process-level metric-capture components."""
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
    tenant_id: str | None = None,
    routed_triple: RoutingDecisionTuple | None = None,
    bypass_guardrails: bool | None = None,
    turn_tracker: TurnTracker | None = None,
    dead_air_detector: DeadAirDetector | None = None,
    cost_tracker: CostTracker | None = None,
) -> str:
    """Bind a LiveKit ``AgentSession`` to the metric-capture pipeline."""
    tracker = turn_tracker if turn_tracker is not None else _active_turn_tracker
    detector = (
        dead_air_detector
        if dead_air_detector is not None
        else _active_dead_air_detector
    )
    ct = cost_tracker if cost_tracker is not None else _active_cost_tracker

    sid = session_id if session_id is not None else get_or_create_session_id()
    if tenant_id is not None:
        set_tenant(tenant_id)
    if routed_triple is not None:
        set_routing_decision(routed_triple)
    if bypass_guardrails is not None:
        set_guardrails_bypassed(bypass_guardrails)

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


def _default_agent_id() -> str:
    """Resolve the agent label: explicit env > hostname > a constant."""
    import socket

    return os.environ.get("VOICEGW_AGENT_ID") or socket.gethostname() or "agent"


def _resolve_room(session: Any) -> str | None:
    """Best-effort LiveKit room name for probe correlation.

    ``voicegw livekit latency`` dispatches an agent to a throwaway room and
    reads the STT/LLM/TTS + turn-detection split back by that room name, so the
    captured rows must carry it. Prefer an explicit ``session._vg_room`` (tests
    / advanced callers), then the running LiveKit job context. Returns None off
    a job (nothing to correlate; the rows simply carry no room), never raises.
    """
    room = getattr(session, "_vg_room", None)
    if isinstance(room, str) and room:
        return room
    try:
        from livekit.agents import get_job_context

        ctx = get_job_context(required=False)
    except Exception:  # noqa: BLE001 - livekit not installed / no job context
        return None
    name = getattr(getattr(ctx, "room", None), "name", None)
    return name if isinstance(name, str) and name else None


def _build_default_sink(
    collector_url: str | None,
    api_key: str | None,
    db_path: str | None = None,
) -> Sink:
    """Build the sink for attach() from env/args.

    Single-node default is a LocalSqliteSink over the embedded StorageService.
    Fleet mode (``collector_url`` set) uses the RemoteCollectorSink, which
    batches rows and pushes them to the collector's ``/v1/ingest``. ``db_path``
    overrides the local SQLite path (falls back to ``VOICEGW_DB_PATH`` then the
    default) and is ignored on the fleet branch.
    """
    if collector_url:
        from voicegateway.services.sinks import RemoteCollectorSink

        return RemoteCollectorSink(collector_url, api_key)

    from voicegateway.services.sinks import LocalSqliteSink
    from voicegateway.services.storage_service import StorageService

    resolved_path = db_path or os.environ.get("VOICEGW_DB_PATH") or DEFAULT_DB_PATH
    return LocalSqliteSink(StorageService(resolved_path))


# Strong refs to in-flight session-close finalize tasks; the event loop only
# weak-refs scheduled tasks, so without this a close-time reconcile/flush can be
# GC'd mid-write (the hazard MetricCapture._schedule guards against for writes).
_close_tasks: set[Any] = set()


def _on_close_task_done(task: Any) -> None:
    _close_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("attach: session close finalize failed", exc_info=exc)


def attach(
    session: Any,
    *,
    project: str = "default",
    agent_id: str | None = None,
    tenant_id: str | None = None,
    collector_url: str | None = None,
    api_key: str | None = None,
    sink: Sink | None = None,
    room: str | None = None,
) -> str:
    """Attach VoiceGateway to an existing LiveKit ``AgentSession`` in one call.

    The *observe* tier: works for ANY plugin (native LiveKit,
    ``livekit.agents.inference``, or ``voicegateway.inference``) by subscribing
    to the non-deprecated per-component ``metrics_collected`` events. Captures
    per-call cost + latency + errors and writes them through a ``Sink`` (local
    SQLite by default, or a collector when configured via ``collector_url`` /
    ``VOICEGW_COLLECTOR_URL``).

    Args:
        session: the ``AgentSession`` to observe.
        project: project id to tag rows with.
        agent_id: fleet label; defaults to ``VOICEGW_AGENT_ID`` or hostname.
        tenant_id: optional tenant attribution.
        collector_url / api_key: fleet push target (env fallbacks).
        sink: advanced/testing override; defaults to local or remote per env.
        room: LiveKit room name for probe correlation; auto-resolved from the
            running job context when omitted (``voicegw livekit latency`` reads
            the STT/LLM/TTS split back by this).

    Returns:
        The correlation session id stamped on every captured row.
    """
    import asyncio

    from voicegateway.fleet.worker import bump_active
    from voicegateway.inference.session.capture import MetricCapture
    from voicegateway.middleware.cost_tracker_middleware import CostTracker

    resolved_agent_id = agent_id or _default_agent_id()
    resolved_collector = collector_url or os.environ.get("VOICEGW_COLLECTOR_URL")
    resolved_key = api_key or os.environ.get("VOICEGW_API_KEY")
    resolved_room = room or _resolve_room(session)
    session_id = get_or_create_session_id()
    if tenant_id is not None:
        set_tenant(tenant_id)
    if sink is None:
        sink = _build_default_sink(resolved_collector, resolved_key)

    cost_tracker = CostTracker(sink)
    capture = MetricCapture(
        cost_tracker=cost_tracker,
        sink=sink,
        project=project,
        agent_id=resolved_agent_id,
        session_id=session_id,
        tenant_id=tenant_id,
        room=resolved_room,
    )
    capture.bind(session)

    # Mark this worker busy for the fleet roster (a no-op unless register_worker
    # was called); the close path drops it back toward idle.
    bump_active(1)

    # Expose for graceful shutdown + tests; flush in-flight writes on close.
    try:
        session._vg_capture = capture
    except Exception:  # noqa: BLE001 - real session may forbid attribute set
        logger.debug("attach: could not stash capture on session", exc_info=True)

    async def _finish() -> None:
        # Reconcile cumulative session.usage against the per-call rows, drain
        # in-flight writes, then flush the sink so a buffered RemoteCollectorSink
        # sub-batch is pushed before shutdown. A graceful close loses nothing.
        bump_active(-1)
        await capture.reconcile(session)
        await capture.drain()
        await sink.flush()

    def _on_close(*_args: Any, **_kwargs: Any) -> None:
        try:
            task = asyncio.ensure_future(_finish())
        except RuntimeError:
            return
        _close_tasks.add(task)
        task.add_done_callback(_on_close_task_done)
        try:
            session._vg_close_task = task
        except Exception:  # noqa: BLE001 - real session may forbid attribute set
            pass

    on = getattr(session, "on", None)
    if callable(on):
        on("close", _on_close)

    return session_id


__all__ = [
    "attach",
    "attach_session",
    "register_components",
    "reset_components",
]
