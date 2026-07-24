"""``attach_session`` helper: opt-in escape hatch for non-standard worker patterns."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from voicegateway.inference.session.context import (
    RoutingDecisionTuple,
    get_or_create_session_id,
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


def _resolve_channel(session: Any) -> str | None:
    """Best-effort telephony-vs-web classification for the dashboard's per-call chip.

    A SIP remote participant means a phone call; any other remote participant means a web
    session. Prefers an explicit ``session._vg_channel`` (tests / advanced callers), then the
    running LiveKit job context. Returns None when it cannot tell (off a job, or no participant
    has joined yet), so the row simply carries no channel rather than a wrong guess.
    """
    ch = getattr(session, "_vg_channel", None)
    if isinstance(ch, str) and ch:
        return ch
    try:
        from livekit.agents import get_job_context

        ctx = get_job_context(required=False)
    except Exception:  # noqa: BLE001 - livekit not installed / no job context
        return None
    participants = getattr(getattr(ctx, "room", None), "remote_participants", None)
    if not participants:
        return None
    try:
        from livekit import rtc

        sip_kind = rtc.ParticipantKind.PARTICIPANT_KIND_SIP
    except Exception:  # noqa: BLE001 - older livekit without the kind enum
        sip_kind = None
    saw_participant = False
    for participant in participants.values():
        saw_participant = True
        if sip_kind is not None and getattr(participant, "kind", None) == sip_kind:
            return "telephony"
    return "web" if saw_participant else None


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
    project: str | None = None,
    agent_id: str | None = None,
    tenant_id: str | None = None,
    channel: str | None = None,
    collector_url: str | None = None,
    api_key: str | None = None,
    sink: Sink | None = None,
    room: str | None = None,
    heartbeat: bool = False,
) -> str:
    """Attach VoiceGateway to a LiveKit ``AgentSession`` or Pipecat ``PipelineTask``.

    The *observe* tier: a single, passive meter for cost + latency. ``attach()``
    detects the target's framework (by type/module, without eager framework
    imports) and installs the matching observer:

    - **LiveKit** ``AgentSession``: subscribes to the non-deprecated
      per-component ``metrics_collected`` events (works for any plugin).
    - **Pipecat** ``PipelineTask``: registers a ``VoiceGatewayObserver`` that
      maps ``MetricsFrame``s and derives STT duration from audio frames.

    Both paths write per-call cost + latency through a ``Sink`` (local SQLite by
    default, or a collector when ``collector_url`` / ``VOICEGW_COLLECTOR_URL`` is
    set), and both lazily import the framework they detect so ``import
    voicegateway`` stays framework-free.

    Args:
        session: the ``AgentSession`` (LiveKit) or ``PipelineTask`` (Pipecat).
        project: project id to tag rows with. Resolution order: explicit
            ``project=`` argument, then the ``VOICEGW_PROJECT`` environment
            variable, then ``"default"``.
        agent_id: fleet label; defaults to ``VOICEGW_AGENT_ID`` or hostname.
        tenant_id: optional tenant attribution.
        channel: ``"telephony"`` | ``"web"``; auto-detected from the transport
            when omitted.
        collector_url / api_key: fleet push target (env fallbacks).
        sink: advanced/testing override; defaults to local or remote per env.
        room: LiveKit room name for probe correlation; auto-resolved from the
            running job context when omitted (``voicegw livekit latency`` reads
            the STT/LLM/TTS split back by this). Ignored on the Pipecat path.
        heartbeat: register this process in the fleet roster and heartbeat its
            presence, so it shows in the dashboard's Fleet/Agents view. Uses the
            collector when one is configured, else writes to the shared local DB
            (``VOICEGW_DB_PATH`` / the default) the co-located dashboard reads. On
            LiveKit it also flips idle<->busy per session; on Pipecat it reports
            presence only. Best for SINGLE-process agents (Pipecat / the LiveKit
            thread executor), where attach is the sole writer. In the LiveKit
            process-executor model (``agent dev``) attach runs in a per-call job
            subprocess, so to show the worker while idle call
            ``register_worker("agent", local=True)`` at your ``__main__`` boot
            instead, and do not also pass ``heartbeat=True`` there (the subprocess
            would become a second writer of the same roster row).

    Returns:
        The correlation session id stamped on every captured row.
    """
    resolved_project = project or os.environ.get("VOICEGW_PROJECT") or "default"

    from voicegateway._frameworks import detect_framework

    if detect_framework(session) == "pipecat":
        return _attach_pipecat(
            session,
            project=resolved_project,
            agent_id=agent_id,
            tenant_id=tenant_id,
            channel=channel,
            collector_url=collector_url,
            api_key=api_key,
            sink=sink,
            heartbeat=heartbeat,
        )
    return _attach_livekit(
        session,
        project=resolved_project,
        agent_id=agent_id,
        tenant_id=tenant_id,
        collector_url=collector_url,
        api_key=api_key,
        sink=sink,
        room=room,
        heartbeat=heartbeat,
    )


def _attach_livekit(
    session: Any,
    *,
    project: str = "default",
    agent_id: str | None = None,
    tenant_id: str | None = None,
    collector_url: str | None = None,
    api_key: str | None = None,
    sink: Sink | None = None,
    room: str | None = None,
    heartbeat: bool = False,
) -> str:
    """LiveKit ``attach()`` body: bind ``MetricCapture`` to an ``AgentSession``."""
    import asyncio

    from voicegateway.fleet.worker import bump_active
    from voicegateway.inference.session.capture import MetricCapture
    from voicegateway.middleware.cost_tracker_middleware import CostTracker

    resolved_agent_id = agent_id or _default_agent_id()
    resolved_collector = collector_url or os.environ.get("VOICEGW_COLLECTOR_URL")
    resolved_key = api_key or os.environ.get("VOICEGW_API_KEY")
    resolved_room = room or _resolve_room(session)
    resolved_channel = _resolve_channel(session)
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
        channel=resolved_channel,
    )
    capture.bind(session)

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

    if heartbeat:
        # Opt into the fleet roster: register + start the heartbeat (local-DB or
        # collector per env). ensure_registered never clobbers a register_worker
        # already done at __main__ boot, so both can coexist.
        from voicegateway.fleet.worker import ensure_registered

        ensure_registered(
            resolved_agent_id,
            project=project,
            tenant_id=tenant_id,
            collector_url=resolved_collector,
            api_key=resolved_key,
            local=True,
        )

    on = getattr(session, "on", None)
    if callable(on):
        # Only mark this worker busy for the fleet roster once we have a close
        # handler to drop it back toward idle; pairing the +1 with the -1 means a
        # session that cannot signal close never pins the worker "busy" forever.
        # (No-op unless register_worker/ensure_registered ran.)
        bump_active(1)
        on("close", _on_close)

    return session_id


# --- Pipecat ---------------------------------------------------------------

# Provider modules/serializers that indicate a telephony (phone) channel. Daily,
# WebRTC, and websocket transports are treated as web.
_PIPECAT_TELEPHONY_TOKENS: tuple[str, ...] = (
    "twilio",
    "telnyx",
    "plivo",
    "exotel",
    "genesys",
    "vonage",
)


def _iter_pipecat_processors(pipeline: Any) -> Any:
    """Yield every processor in a pipecat pipeline, descending nested pipelines.

    ``Pipeline.processors`` includes source/sink wrappers and any nested
    pipelines; we descend so an STT service inside a sub-pipeline is still found.
    Best-effort and defensive: a shape we do not recognize simply yields nothing.
    """
    seen: set[int] = set()
    stack = [pipeline]
    while stack:
        node = stack.pop()
        if node is None or id(node) in seen:
            continue
        seen.add(id(node))
        children = getattr(node, "processors", None)
        if children:
            for child in children:
                yield child
                stack.append(child)


def _detect_pipecat_channel(task: Any) -> str | None:
    """Best-effort telephony-vs-web from the pipeline's transport/serializer.

    A telephony serializer/transport module (Twilio/Telnyx/Plivo/...) means a
    phone call; a Daily/WebRTC/websocket transport means web. Returns None when
    nothing conclusive is found, so the row simply carries no channel.
    """
    pipeline = getattr(task, "pipeline", None)
    if pipeline is None:
        return None
    saw_transport = False
    for proc in _iter_pipecat_processors(pipeline):
        module = (type(proc).__module__ or "").lower()
        if "transports" in module or "serializers" in module:
            saw_transport = True
            for token in _PIPECAT_TELEPHONY_TOKENS:
                if token in module:
                    return "telephony"
        # A transport may hold a serializer as an attribute rather than as its
        # own processor; sniff a ``_serializer`` module too.
        serializer = getattr(proc, "_serializer", None) or getattr(
            proc, "serializer", None
        )
        if serializer is not None:
            ser_module = (type(serializer).__module__ or "").lower()
            saw_transport = True
            for token in _PIPECAT_TELEPHONY_TOKENS:
                if token in ser_module:
                    return "telephony"
    return "web" if saw_transport else None


def _register_pipecat_stt(observer: Any, task: Any) -> None:
    """Register every STT service found in the task's pipeline with the observer."""
    from voicegateway.inference.pipecat.observer import _service_base_modality

    pipeline = getattr(task, "pipeline", None)
    if pipeline is None:
        return
    for proc in _iter_pipecat_processors(pipeline):
        try:
            if _service_base_modality(proc) == "stt":
                observer.register_stt(proc)
        except Exception:  # noqa: BLE001 - never let discovery break attach
            logger.debug(
                "attach(pipecat): STT discovery skipped a processor", exc_info=True
            )


def _attach_pipecat(
    task: Any,
    *,
    project: str = "default",
    agent_id: str | None = None,
    tenant_id: str | None = None,
    channel: str | None = None,
    collector_url: str | None = None,
    api_key: str | None = None,
    sink: Sink | None = None,
    heartbeat: bool = False,
) -> str:
    """Register a ``VoiceGatewayObserver`` on a Pipecat ``PipelineTask``.

    Mirrors the LiveKit ``attach`` path: builds the same sink, stamps the tenant
    ContextVar, and returns the correlation session id. The observer is the sole
    meter; it finalizes itself on the pipeline ``EndFrame`` (drain + flush).
    """
    from voicegateway.inference.pipecat.observer import VoiceGatewayObserver

    resolved_agent_id = agent_id or _default_agent_id()
    resolved_collector = collector_url or os.environ.get("VOICEGW_COLLECTOR_URL")
    resolved_key = api_key or os.environ.get("VOICEGW_API_KEY")
    session_id = get_or_create_session_id()
    if tenant_id is not None:
        set_tenant(tenant_id)
    if sink is None:
        sink = _build_default_sink(resolved_collector, resolved_key)

    resolved_channel = channel or _detect_pipecat_channel(task)

    observer = VoiceGatewayObserver(
        sink=sink,
        project=project,
        agent_id=resolved_agent_id,
        session_id=session_id,
        tenant_id=tenant_id,
        channel=resolved_channel,
    )
    _register_pipecat_stt(observer, task)

    # Register the observer on the task. Prefer ``add_observer`` (1.5.0 API);
    # fall back to appending to an observers list if a variant exposes only that.
    add_observer = getattr(task, "add_observer", None)
    if callable(add_observer):
        add_observer(observer)
    else:  # pragma: no cover - 1.5.0 exposes add_observer
        observers = getattr(task, "_observers", None)
        if isinstance(observers, list):
            observers.append(observer)
        else:
            raise TypeError(
                "attach(pipecat): PipelineTask exposes no add_observer/observers; "
                "pass Observer(...) to PipelineTask(observers=[...]) instead."
            )

    # Expose for graceful shutdown + tests.
    try:
        task._vg_observer = observer
    except Exception:  # noqa: BLE001 - real task may forbid attribute set
        logger.debug("attach(pipecat): could not stash observer on task", exc_info=True)

    if heartbeat:
        # Fleet presence for a Pipecat worker. (Pipecat has no session open/close
        # counter, so this reports presence; idle<->busy bumping is LiveKit-only.)
        from voicegateway.fleet.worker import ensure_registered

        ensure_registered(
            resolved_agent_id,
            project=project,
            tenant_id=tenant_id,
            collector_url=resolved_collector,
            api_key=resolved_key,
            local=True,
        )

    return session_id


__all__ = [
    "attach",
    "attach_session",
    "register_components",
    "reset_components",
]
