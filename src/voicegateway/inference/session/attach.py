"""``attach_session`` helper: opt-in escape hatch for non-standard worker patterns."""

from __future__ import annotations

import asyncio
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


# Strong refs to in-flight turn-event tasks. asyncio only weak-refs scheduled
# tasks, so without this a turn write can be collected mid-flight, exactly as
# _close_tasks below guards the close-time finalize.
_turn_tasks: set[Any] = set()


def _schedule_turn_event(coro: Any, what: str) -> None:
    """Run a tracker coroutine from a SYNCHRONOUS event callback.

    LiveKit does not accept coroutine functions as event handlers. Its
    ``EventEmitter.on`` raises outright:

        ValueError: Cannot register an async callback with `.on()`.
        Use `asyncio.create_task` within your synchronous callback instead.

    So every handler here is a plain ``def`` that schedules the await, which is
    what that message asks for. Registering ``async def`` handlers, as this
    module used to, raised on any real ``AgentSession``; it survived only
    because the test doubles accept anything from ``.on()``.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop: a synchronous test rig, or a callback fired off-loop.
        # Dropping is correct here, and saying so beats a silent no-op.
        logger.debug("attach: no running loop, dropping turn event %s", what)
        coro.close()
        return
    task = loop.create_task(coro)
    _turn_tasks.add(task)
    task.add_done_callback(_on_turn_task_done)


def _on_turn_task_done(task: Any) -> None:
    _turn_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("attach: turn event failed", exc_info=exc)


def _emit_user_started(session_id: str, tracker: TurnTracker) -> Any:
    """Return a sync event handler that forwards user-started events."""

    def _handler(*_args: Any, **_kwargs: Any) -> None:
        _schedule_turn_event(
            tracker.on_user_started_speaking(session_id=session_id),
            "user_started_speaking",
        )

    return _handler


def _emit_user_stopped(session_id: str, tracker: TurnTracker) -> Any:
    def _handler(*_args: Any, **_kwargs: Any) -> None:
        _schedule_turn_event(
            tracker.on_user_stopped_speaking(session_id=session_id),
            "user_stopped_speaking",
        )

    return _handler


def _emit_agent_started(session_id: str, tracker: TurnTracker) -> Any:
    def _handler(*_args: Any, **_kwargs: Any) -> None:
        _schedule_turn_event(
            tracker.on_agent_audio_first_frame(session_id=session_id),
            "agent_started_speaking",
        )

    return _handler


def _emit_agent_stopped(session_id: str, tracker: TurnTracker) -> Any:
    def _handler(*_args: Any, **_kwargs: Any) -> None:
        _schedule_turn_event(
            tracker.on_agent_audio_last_frame(session_id=session_id),
            "agent_stopped_speaking",
        )

    return _handler


def _emit_close(
    session_id: str,
    tracker: TurnTracker,
    detector: DeadAirDetector | None,
    cost_tracker: CostTracker | None,
) -> Any:
    """Close handler: flush tracker, stop detector, finalize cost_tracker.

    Sync wrapper for the same reason as the emitters above: LiveKit refuses a
    coroutine function at ``.on()``.
    """

    async def _run() -> None:
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

    def _handler(*_args: Any, **_kwargs: Any) -> None:
        _schedule_turn_event(_run(), "close")

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
            "attach_session(%s): no TurnTracker; turn events will be dropped. "
            "Pass turn_tracker=..., or use attach(session, turns=True), which "
            "builds one wired to the sink for you.",
            sid,
        )
        return sid

    # Shared with attach(turns=True) so both paths get the same event set,
    # including the LiveKit 1.6 onset fallback.
    _bind_turn_events(agent_session, sid, tracker)
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


def _resolve_dispatch_name(session: Any) -> str | None:
    """Best-effort LiveKit *dispatch* name for this job, for probe targeting.

    This is ``Job.agent_name``: the name an explicit dispatch has to match. It is
    set at worker registration inside the agent's own process (``WorkerOptions``
    / ``ServerOptions``), so it can only be OBSERVED here, never chosen. It is
    also distinct from ``attach(agent_id=...)`` and from
    ``register_worker(agent_name=...)``, both of which are VoiceGateway labels
    that LiveKit has never heard of.

    Three outcomes, all meaningful:

    - a non-empty string: the worker registered an agent_name, so the dashboard
      can dispatch a probe to it by that name.
    - ``""``: LiveKit's value for a worker registered WITHOUT an agent_name,
      which means automatic dispatch (the worker joins every new room). A probe
      reaches it by creating a room, with no explicit dispatch.
    - ``None``: not observable (no job context, livekit not installed, older
      protocol without the field). The dashboard says so instead of guessing.

    Prefers an explicit ``session._vg_dispatch_name`` (tests / advanced callers).
    Never raises.
    """
    explicit = getattr(session, "_vg_dispatch_name", None)
    if isinstance(explicit, str):
        return explicit
    try:
        from livekit.agents import get_job_context

        ctx = get_job_context(required=False)
    except Exception:  # noqa: BLE001 - livekit not installed / no job context
        return None
    name = getattr(getattr(ctx, "job", None), "agent_name", None)
    return name if isinstance(name, str) else None


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
    transcript: bool = True,
    snapshots: bool = False,
    turns: bool = True,
    dead_air: bool = True,
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
        transcript: capture the call transcript (default on). On close, the
            user/agent text turns are read from the framework's conversation
            history and written to the local store, so the Calls page can show
            the conversation. Pass ``transcript=False`` to disable per attach, or
            set ``VOICEGW_TRANSCRIPTS=0`` to disable capture fleet-wide (the
            kill-switch wins over the argument). Captures to the local SQLite the
            co-located dashboard reads; currently LiveKit-only (the Pipecat path
            accepts the flag but does not capture transcripts yet).
        snapshots: capture conversation-state snapshots (default OFF). When on,
            a snapshot is written at each completed message and each resolved
            tool call, carrying the system prompt, the message history, and the
            tool's arguments and result. ``voicegw replay`` and the dashboard's
            replay view read these back. Off by default because that is a
            strictly larger disclosure than ``transcript``: it captures the
            operator's own prompt and whatever payloads their tools handle, not
            only what the caller said. Set ``VOICEGW_SNAPSHOTS=0`` to force it
            off fleet-wide (the kill-switch wins over the argument). Message
            snapshots are rate-capped to one per second; tool-call snapshots
            bypass that cap because they are rare and are the point. Requires a
            local sink: a remote collector has no replay tables, so capture is
            skipped there rather than buffering rows nothing can flush.
            LiveKit-only, like transcripts.
        turns: capture per-turn speech boundaries (default ON). One row per
            caller/agent exchange: the turn index and the four speech timestamps,
            from which ``response_speed_ms`` is derived. This is what
            ``e2e_ms`` and the ``turns`` list on ``/v1/rooms/{room}/latency``
            are computed from, along with the Conversation tab's response-speed
            and talk-over columns; all of them read empty without it. On by
            default because a turn row is six integers and carries no utterance,
            no prompt and no tool payload, so the disclosure argument that keeps
            ``snapshots`` off does not apply. Unlike transcripts it works
            against a remote collector too, via ``POST /v1/ingest/turns``. Set
            ``VOICEGW_TURNS=0`` to force it off fleet-wide (the kill-switch wins
            over the argument). LiveKit-only: the Pipecat path accepts the flag
            but has no equivalent speech-boundary events yet.
        dead_air: watch for silences longer than the threshold (default ON).
            Writes one row per observed silence, which
            ``GET /api/sessions/{id}/dead_air`` and the dashboard read back.
            Driven by the same four speech events as ``turns``, but its own flag
            because it POLLS: a task per session at one second for the life of
            the call, where turn capture costs nothing between events. A
            dead-air row is also an alert rather than a chart cell, so wanting
            one feature without the other is coherent in both directions.
            Threshold comes from the project's
            ``metrics.dead_air_threshold_seconds`` (default 3.0s). Speech is
            never dead air: while either party is speaking the clock reports
            now, so a long utterance cannot trip it. Set ``VOICEGW_DEAD_AIR=0``
            to force it off fleet-wide. LiveKit-only, like ``turns``.

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
            transcript=transcript,
            snapshots=snapshots,
            turns=turns,
            dead_air=dead_air,
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
        transcript=transcript,
        snapshots=snapshots,
        turns=turns,
        dead_air=dead_air,
    )


def _transcripts_enabled(param: bool) -> bool:
    """Whether to capture transcripts: the ``attach(transcript=)`` flag, unless
    the ``VOICEGW_TRANSCRIPTS`` kill-switch explicitly disables it fleet-wide."""
    env = os.environ.get("VOICEGW_TRANSCRIPTS")
    if env is not None and env.strip().lower() in ("0", "false", "no", "off"):
        return False
    return param


def _turns_enabled(param: bool) -> bool:
    """Whether to capture conversation turns. Same shape as transcripts above.

    DEFAULT ON, like transcripts and unlike snapshots. A turn row is six
    integers: session id, turn index, and the caller/agent speech boundaries.
    It carries no utterance, no prompt and no tool payload, so the disclosure
    argument that makes snapshots opt-in does not apply. It is what
    ``e2e_ms`` on ``/v1/rooms/{room}/latency`` and the Conversation tab's
    response-speed and talk-over columns are computed from, and all of them
    read empty without it.
    """
    env = os.environ.get("VOICEGW_TURNS")
    if env is not None and env.strip().lower() in ("0", "false", "no", "off"):
        return False
    return param


def _dead_air_enabled(param: bool) -> bool:
    """Whether to watch for dead air. Same shape as turns above.

    Its own flag rather than riding ``turns``, although both are driven by the
    same four speech events, because this one POLLS: it runs a task per session
    at ``poll_interval_seconds`` for the life of the call, where turn capture
    costs nothing between events. A standing per-session cost should be
    switchable on its own. A dead-air row is also an alert someone acts on
    rather than a cell on a chart, so wanting one without the other is coherent
    in both directions.
    """
    env = os.environ.get("VOICEGW_DEAD_AIR")
    if env is not None and env.strip().lower() in ("0", "false", "no", "off"):
        return False
    return param


_DEFAULT_DEAD_AIR_THRESHOLD_S = 3.0
_dead_air_threshold_cache: dict[str | None, float] = {}


def _dead_air_threshold_s(project: str | None) -> float:
    """Seconds of silence before a dead-air event fires, for this project.

    ``metrics`` hangs off ProjectConfig, so ``dead_air_threshold_seconds`` is
    per project, exactly like the two knobs beside it. Same best-effort load and
    per-project cache as ``_turn_flush_size``.
    """
    if project in _dead_air_threshold_cache:
        return _dead_air_threshold_cache[project]
    threshold = _DEFAULT_DEAD_AIR_THRESHOLD_S
    try:
        from voicegateway.core.config import GatewayConfig

        cfg = GatewayConfig.load(required=False)
        pcfg = (cfg.projects or {}).get(project) if project else None
        if pcfg is not None:
            threshold = float(pcfg.metrics.dead_air_threshold_seconds)
    except Exception:  # noqa: BLE001 - a bad config must not break attach()
        logger.debug("attach: reading dead_air_threshold_seconds failed", exc_info=True)
    if threshold <= 0:
        threshold = _DEFAULT_DEAD_AIR_THRESHOLD_S
    _dead_air_threshold_cache[project] = threshold
    return threshold


class _SpeechActivity:
    """Last-activity clock for dead-air detection, in the detector's own units.

    Returns ``time.monotonic() * 1000``, which is exactly what
    ``DeadAirDetector._watch`` compares against and what ``TurnTracker._now_ms``
    already produces, so no conversion and no clock mismatch.

    The part that matters: **while anyone is speaking, activity is now**. A
    probe that only recorded discrete events would keep reporting the onset
    timestamp for the whole utterance, so a caller talking for longer than the
    threshold would trip a dead-air event mid-sentence. Speech is not dead air,
    and a false alert is worse than a missing one because somebody acts on it.

    Two booleans rather than a counter, so a duplicated event is harmless. That
    is not hypothetical: LiveKit 1.6 signals onset through
    ``user_state_changed`` while older builds emit the discrete event, and a
    build emitting both would leave a counter permanently above zero, which
    would silently mean dead air could never fire again.
    """

    def __init__(self) -> None:
        self._last_ms = self._now_ms()
        self._caller_speaking = False
        self._agent_speaking = False

    @staticmethod
    def _now_ms() -> int:
        import time

        return int(time.monotonic() * 1000)

    def _touch(self) -> None:
        self._last_ms = self._now_ms()

    def caller_started(self) -> None:
        self._caller_speaking = True
        self._touch()

    def caller_stopped(self) -> None:
        self._caller_speaking = False
        self._touch()

    def agent_started(self) -> None:
        self._agent_speaking = True
        self._touch()

    def agent_stopped(self) -> None:
        self._agent_speaking = False
        self._touch()

    def probe(self, _session_id: str) -> int | None:
        """The ActivityProbe. Sync by contract, so it only reads memory."""
        if self._caller_speaking or self._agent_speaking:
            return self._now_ms()
        return self._last_ms


def _build_dead_air_detector(sink: Sink, project: str | None) -> tuple[Any, Any]:
    """A detector whose events persist through the sink, and its activity clock.

    Returns ``(detector, activity)``; the caller binds ``activity`` to the same
    speech events turn capture uses. Without an ``on_event`` the detector falls
    back to a no-op that logs "dropped (no repository wired)" at debug, which is
    why ``dead_air_events`` was always empty.
    """
    from voicegateway.middleware.dead_air_detector_middleware import (
        DeadAirDetector,
        DeadAirEvent,
    )

    activity = _SpeechActivity()

    async def _on_event(event: DeadAirEvent) -> None:
        await sink.log_dead_air([event])

    detector = DeadAirDetector(
        activity_probe=activity.probe,
        on_event=_on_event,
        threshold_seconds=_dead_air_threshold_s(project),
    )
    return detector, activity


_DEFAULT_TURN_FLUSH_SIZE = 25
_turn_flush_size_cache: dict[str | None, int] = {}


def _turn_flush_size(project: str | None) -> int:
    """How many turns TurnTracker buffers before writing through the sink.

    ``metrics`` hangs off ProjectConfig rather than GatewayConfig, so
    ``turn_buffer_flush_size`` is a per-project knob and needs the project
    ``attach()`` was called with. Loaded with ``required=False`` because
    ``attach()`` is library-side and must work with no config file at all;
    absent one, this is the schema default, which is the same number.

    Cached per project: a busy worker calls ``attach()`` once per session and
    should not stat the filesystem each time.
    """
    if project in _turn_flush_size_cache:
        return _turn_flush_size_cache[project]
    size = _DEFAULT_TURN_FLUSH_SIZE
    try:
        from voicegateway.core.config import GatewayConfig

        cfg = GatewayConfig.load(required=False)
        pcfg = (cfg.projects or {}).get(project) if project else None
        if pcfg is not None:
            size = int(pcfg.metrics.turn_buffer_flush_size)
    except Exception:  # noqa: BLE001 - a bad config must not break attach()
        logger.debug("attach: reading turn_buffer_flush_size failed", exc_info=True)
    if size < 1:
        size = _DEFAULT_TURN_FLUSH_SIZE
    _turn_flush_size_cache[project] = size
    return size


def _build_turn_tracker(sink: Sink, project: str | None) -> Any:
    """A TurnTracker whose flush writes turns through the sink.

    The flush callback is what was missing: TurnTracker's default is a no-op
    that logs "turns dropped (no repository wired)" at debug, so every reader of
    the turns table read empty no matter how the tracker was bound.
    """
    from voicegateway.middleware.turn_tracker_middleware import TurnRow, TurnTracker

    async def _flush(rows: list[TurnRow]) -> None:
        await sink.log_turns(rows)

    return TurnTracker(flush_callback=_flush, flush_size=_turn_flush_size(project))


def _bind_turn_events(
    session: Any,
    session_id: str,
    tracker: Any,
    activity: Any = None,
) -> None:
    """Wire the speech-boundary events onto an AgentSession.

    **Every boundary is driven by a state-change event, because the discrete
    ones do not exist.** ``user_started_speaking``, ``user_stopped_speaking``,
    ``agent_started_speaking`` and ``agent_stopped_speaking`` are absent from
    ``EventTypes`` in livekit-agents 1.5.7 AND 1.6.9, and the names appear
    nowhere in the package on either. This is not a 1.6 regression; nothing in
    the supported ``>=1.5,<1.7`` range has ever emitted them.

    Turn capture previously bridged only the caller's ONSET, which is why the
    table came back empty rather than partially filled: a turn is closed by
    ``on_agent_audio_first_frame``, so with no agent-start signal no turn ever
    closed and nothing was ever buffered.

    What LiveKit does emit is ``user_state_changed`` and
    ``agent_state_changed``, each carrying ``old_state`` and ``new_state``:

    - ``UserState``  = speaking | listening | away
    - ``AgentState`` = initializing | idle | listening | thinking | speaking

    So a boundary is a transition INTO or OUT OF ``speaking``. Stops guard on
    ``old_state == "speaking"`` rather than on a specific destination: the
    caller can go speaking -> away as well as speaking -> listening, and both
    are the same event (they stopped talking), while listening -> away is not a
    stop at all because no speech was in flight. Keying on the origin gets all
    three right; keying on the destination drops the away case.

    The four discrete names stay bound. They cost nothing on LiveKit, where they
    never fire, and they are the path for any other framework or future build
    that does emit them. Double-binding is safe because every tracker entry
    point is first-wins: ``on_user_started_speaking`` and
    ``on_user_stopped_speaking`` only set their field when it is None,
    ``on_agent_audio_first_frame`` returns early once the turn is closed, and
    ``on_agent_audio_last_frame`` only fills an unset end.

    ``activity`` is the optional dead-air clock, updated independently of
    ``tracker`` so the two features stay separately switchable. ``tracker`` may
    be None when only dead air is on.
    """
    on = getattr(session, "on", None)
    if not callable(on):
        logger.debug("attach: session has no .on(); speech events not bound")
        return

    started = _emit_user_started(session_id, tracker) if tracker is not None else None
    stopped = _emit_user_stopped(session_id, tracker) if tracker is not None else None
    agent_started = (
        _emit_agent_started(session_id, tracker) if tracker is not None else None
    )
    agent_stopped = (
        _emit_agent_stopped(session_id, tracker) if tracker is not None else None
    )

    def _user_started(event: Any = None, *_a: Any, **_k: Any) -> None:
        if activity is not None:
            activity.caller_started()
        if started is not None:
            started(event)

    def _user_stopped(event: Any = None, *_a: Any, **_k: Any) -> None:
        if activity is not None:
            activity.caller_stopped()
        if stopped is not None:
            stopped(event)

    def _agent_started(event: Any = None, *_a: Any, **_k: Any) -> None:
        if activity is not None:
            activity.agent_started()
        if agent_started is not None:
            agent_started(event)

    def _agent_stopped(event: Any = None, *_a: Any, **_k: Any) -> None:
        if activity is not None:
            activity.agent_stopped()
        if agent_stopped is not None:
            agent_stopped(event)

    def _on_user_state_changed(event: Any = None, *_a: Any, **_k: Any) -> None:
        old = getattr(event, "old_state", None)
        new = getattr(event, "new_state", None)
        if new == "speaking":
            _user_started(event)
            return
        if old == "speaking":
            # Out of speaking, to listening OR away: the caller stopped talking.
            # An earlier version routed this to the activity clock only, on the
            # worry that it would close a turn on an "away" transition. It does
            # not: on_user_stopped_speaking records the caller's speech END, and
            # the turn is closed by on_agent_audio_first_frame. Withholding it
            # left response_speed_ms measured from the wrong instant.
            _user_stopped(event)

    def _on_agent_state_changed(event: Any = None, *_a: Any, **_k: Any) -> None:
        old = getattr(event, "old_state", None)
        new = getattr(event, "new_state", None)
        if new == "speaking":
            # The boundary that closes a turn. Without it nothing is ever
            # buffered, which is why the table was empty and not merely partial.
            _agent_started(event)
            return
        if old == "speaking":
            # speaking -> idle/listening/thinking are all the agent finishing.
            _agent_stopped(event)

    # State changes: the only ones LiveKit actually emits.
    on("user_state_changed", _on_user_state_changed)
    on("agent_state_changed", _on_agent_state_changed)
    # Discrete events: absent from LiveKit in the supported range, kept for
    # other frameworks and any build that does emit them. Harmless when they
    # never fire, and safe alongside the above because the tracker is first-wins.
    on("user_started_speaking", _user_started)
    on("user_stopped_speaking", _user_stopped)
    on("agent_started_speaking", _agent_started)
    on("agent_stopped_speaking", _agent_stopped)


def _snapshots_enabled(param: bool) -> bool:
    """Whether to capture state snapshots. Same shape as transcripts above.

    DEFAULT OFF, which is the one place this deliberately differs from
    transcripts. A transcript is what the caller said, which the operator
    already has. A snapshot is the SYSTEM PROMPT plus the whole message history
    plus every tool call's arguments and result, so it captures the operator's
    own prompt and whatever payloads their tools handle. That is a strictly
    larger disclosure than a transcript and should be asked for, not assumed.

    The kill-switch still wins over the argument, so a fleet can force it off
    centrally even where an agent passes ``snapshots=True``.
    """
    env = os.environ.get("VOICEGW_SNAPSHOTS")
    if env is not None and env.strip().lower() in ("0", "false", "no", "off"):
        return False
    return param


def _history_items(session: Any) -> list[dict[str, Any]]:
    """The conversation so far, as plain role/text dicts. Never raises."""
    out: list[dict[str, Any]] = []
    try:
        items = getattr(getattr(session, "history", None), "items", None) or []
        for item in items:
            role = getattr(item, "role", None)
            text = getattr(item, "text_content", None)
            if isinstance(role, str) and isinstance(text, str) and text.strip():
                out.append({"role": role, "text": text})
    except Exception:  # noqa: BLE001 - snapshots are never load-bearing
        logger.debug("attach: reading history for snapshot failed", exc_info=True)
    return out


def _system_prompt(session: Any) -> str:
    """The active agent's instructions, or "" when not readable. Never raises."""
    try:
        agent = getattr(session, "current_agent", None) or getattr(
            session, "_agent", None
        )
        instructions = getattr(agent, "instructions", None)
        return instructions if isinstance(instructions, str) else ""
    except Exception:  # noqa: BLE001
        return ""


def _emit_conversation_item(session_id: str, session: Any, snapshotter: Any) -> Any:
    """LiveKit ``conversation_item_added`` -> one rate-capped state snapshot."""

    async def _handler(*_args: Any, **_kwargs: Any) -> None:
        try:
            await snapshotter.on_message_added(
                system_prompt=_system_prompt(session),
                message_history=_history_items(session),
                session_id=session_id,
            )
        except Exception:  # noqa: BLE001 - never let capture break the agent
            logger.debug("attach: snapshot on message add failed", exc_info=True)

    return _handler


def _emit_tools_executed(session_id: str, session: Any, snapshotter: Any) -> Any:
    """LiveKit ``function_tools_executed`` -> one snapshot per resolved call.

    Uses ``on_tool_resolved``, which bypasses the rate cap on purpose: tool
    calls are rare and are the single most useful thing in a replay, so they
    must never be the sample the 1/s cap happens to drop.
    """

    async def _handler(event: Any = None, *_args: Any, **_kwargs: Any) -> None:
        try:
            zipped = getattr(event, "zipped", None)
            pairs = zipped() if callable(zipped) else []
            for call, output in pairs:
                raw = getattr(call, "arguments", "") or ""
                try:
                    import json

                    args = json.loads(raw) if raw else {}
                except (ValueError, TypeError):
                    # Providers send arguments as a JSON string; keep the raw
                    # text rather than dropping the call when it will not parse.
                    args = {"_raw": raw}
                await snapshotter.on_tool_resolved(
                    tool_name=getattr(call, "name", "") or "",
                    tool_args=args if isinstance(args, dict) else {"_raw": raw},
                    result=getattr(output, "output", None),
                    system_prompt=_system_prompt(session),
                    message_history=_history_items(session),
                    session_id=session_id,
                )
        except Exception:  # noqa: BLE001 - never let capture break the agent
            logger.debug("attach: snapshot on tools executed failed", exc_info=True)

    return _handler


async def _capture_transcript_from_history(
    session: Any, session_id: str, storage: Any, tenant_id: str | None
) -> None:
    """Best-effort: persist the call transcript from the framework history.

    Reads the AgentSession's conversation history (populated by close time), keeps
    the user/agent text turns (mapping the ``assistant`` role to ``agent``), and
    writes them to the local transcript store. Never raises: a capture failure
    must not affect the agent.
    """
    try:
        history = getattr(session, "history", None)
        items = getattr(history, "items", None)
        if not items:
            return
        turns: list[tuple[str, str]] = []
        for item in items:
            role = getattr(item, "role", None)
            body = getattr(item, "text_content", None)
            if role in ("user", "assistant") and isinstance(body, str) and body.strip():
                turns.append(("agent" if role == "assistant" else "user", body))
        if turns:
            await storage.write_transcript(session_id, turns, tenant_id=tenant_id)
    except Exception:  # noqa: BLE001 - transcripts are never load-bearing
        logger.debug("attach: transcript capture failed", exc_info=True)


def _build_snapshot_capture(
    sink: Any, tenant_id: str | None, session_id: str
) -> tuple[Any | None, Any | None]:
    """Build the (ReplayCapture, StateSnapshotter) pair, or (None, None).

    These two were written to plug into each other and never were, and the seam
    has a gap that only shows up once they are actually joined.
    ``StateSnapshotter._emit`` resolves the session id, then calls
    ``self._on_snapshot(snapshot.model_dump())`` with that one argument and
    drops it. ``ReplayCapture.record_state_snapshot`` therefore takes
    ``session_id=None`` and falls back to the session ContextVar, so the
    snapshot buffers under whatever id that happens to hold. LiveKit dispatches
    event handlers on tasks that need not carry it, and the id attach() was
    given can differ from the ambient one, so the buffer ends up keyed
    differently from the ``close_session`` that flushes it and the rows are
    silently dropped.

    Rather than widen either component's callback type (nine existing tests pin
    the one-argument shape, and it is a reasonable shape), the id is bound HERE,
    where it is known and unambiguous. attach() builds one pair per session, so
    a closure is exactly the right scope.

    Returns (None, None) when the sink has no local storage. A remote collector
    sink has no replay tables to write into, and the dashboard reads replay from
    the local store, so capturing there would buffer rows nothing could flush.
    """
    storage = getattr(sink, "_storage", None)
    if storage is None:
        return None, None
    from voicegateway.middleware.replay_capture_middleware import ReplayCapture
    from voicegateway.middleware.state_snapshotter_middleware import StateSnapshotter

    async def _flush(events: list[Any]) -> None:
        await storage.write_replay_events(events, tenant_id=tenant_id)

    capture = ReplayCapture(flush_callback=_flush)

    async def _on_snapshot(snapshot: dict[str, Any]) -> None:
        await capture.record_state_snapshot(snapshot, session_id=session_id)

    return capture, StateSnapshotter(on_snapshot=_on_snapshot)


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
    transcript: bool = True,
    snapshots: bool = False,
    turns: bool = True,
    dead_air: bool = True,
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
    resolved_dispatch_name = _resolve_dispatch_name(session)
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
        dispatch_name=resolved_dispatch_name,
    )
    capture.bind(session)

    # Expose for graceful shutdown + tests; flush in-flight writes on close.
    try:
        session._vg_capture = capture
    except Exception:  # noqa: BLE001 - real session may forbid attribute set
        logger.debug("attach: could not stash capture on session", exc_info=True)

    transcript_on = _transcripts_enabled(transcript)
    snapshot_capture, snapshotter = (
        _build_snapshot_capture(sink, tenant_id, session_id)
        if _snapshots_enabled(snapshots)
        else (None, None)
    )
    # Same session_id the RequestRecords above are stamped with. rooms.py joins
    # room -> session_id -> turns, so a tracker keyed on anything else produces
    # turns that exist but never appear in /v1/rooms/{room}/latency.
    turn_tracker = (
        _build_turn_tracker(sink, project) if _turns_enabled(turns) else None
    )
    dead_air_detector, activity = (
        _build_dead_air_detector(sink, project)
        if _dead_air_enabled(dead_air)
        else (None, None)
    )
    # One binding for both: the activity clock rides the same four events the
    # tracker does, and either half may be None.
    if turn_tracker is not None or activity is not None:
        _bind_turn_events(session, session_id, turn_tracker, activity)
    # Expose for tests and for graceful shutdown, as with _vg_capture above.
    for _attr, _value in (
        ("_vg_dead_air", dead_air_detector),
        ("_vg_activity", activity),
    ):
        try:
            setattr(session, _attr, _value)
        except Exception:  # noqa: BLE001 - real session may forbid attribute set
            logger.debug("attach: could not stash %s on session", _attr, exc_info=True)
    if dead_air_detector is not None:

        async def _start_dead_air() -> None:
            # Warm the store BEFORE the watcher can fire. Otherwise the first
            # dead-air event is what triggers migrations, from inside the poll
            # loop, and a "database is locked" there loses that event for good:
            # the detector sets _already_fired before awaiting the callback, so
            # it will not re-fire for the same silence. Reproduced, not feared.
            warm = getattr(sink, "_storage", None)
            if warm is not None:
                try:
                    await warm._ensure_initialized()
                except Exception:  # noqa: BLE001 - dead air is never load-bearing
                    logger.debug("attach: storage warm-up failed", exc_info=True)
            await dead_air_detector.start(session_id)

        # start() spawns the watcher task, so it needs a running loop. attach()
        # is routinely called from sync setup code; there the watcher simply
        # does not start, said out loud rather than failing silently.
        try:
            task = asyncio.get_running_loop().create_task(_start_dead_air())
            _close_tasks.add(task)
            task.add_done_callback(_close_tasks.discard)
        except RuntimeError:
            logger.debug(
                "attach(%s): no running event loop, dead-air watcher not started",
                session_id,
            )

    async def _finish() -> None:
        # Reconcile cumulative session.usage against the per-call rows, drain
        # in-flight writes, then flush the sink so a buffered RemoteCollectorSink
        # sub-batch is pushed before shutdown. A graceful close loses nothing.
        bump_active(-1)
        await capture.reconcile(session)
        await capture.drain()
        if dead_air_detector is not None:
            # Before the flush below, so an event observed in the last poll is
            # already in the sink's buffer when it drains.
            try:
                await dead_air_detector.stop(session_id)
            except Exception:  # noqa: BLE001 - dead air is never load-bearing
                logger.debug("attach: dead-air stop on close failed", exc_info=True)
        if turn_tracker is not None:
            # Before sink.flush(): close_session emits the tail turn and pushes
            # everything under the flush-size threshold into the sink, so the
            # flush below is what actually puts them on the wire.
            try:
                await turn_tracker.close_session(session_id)
            except Exception:  # noqa: BLE001 - turns are never load-bearing
                logger.debug("attach: turn flush on close failed", exc_info=True)
        await sink.flush()
        if snapshot_capture is not None:
            # Final flush of anything still buffered under the flush-size
            # threshold, then drop the per-session state. Before sink.flush()
            # would be wrong: this writes through storage, not the sink.
            try:
                await snapshot_capture.close_session(session_id)
            except Exception:  # noqa: BLE001 - snapshots are never load-bearing
                logger.debug("attach: snapshot flush on close failed", exc_info=True)
        if transcript_on:
            # LocalSqliteSink exposes the storage the co-located dashboard reads;
            # remote/ClickHouse sinks have no local transcript store (None -> skip).
            storage = getattr(sink, "_storage", None)
            if storage is not None:
                await _capture_transcript_from_history(
                    session, session_id, storage, tenant_id
                )

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
            # The roster display name here is the VG agent-id label, not a LiveKit
            # name, so record the dispatch name resolved from the job separately.
            # None off a LiveKit job: the worker stays in the roster but is not
            # probeable by a name it never registered under.
            dispatch_name=resolved_dispatch_name,
        )

    on = getattr(session, "on", None)
    if callable(on):
        # Only mark this worker busy for the fleet roster once we have a close
        # handler to drop it back toward idle; pairing the +1 with the -1 means a
        # session that cannot signal close never pins the worker "busy" forever.
        # (No-op unless register_worker/ensure_registered ran.)
        bump_active(1)
        on("close", _on_close)
        if snapshotter is not None:
            # The two events that carry what a state snapshot needs. Neither is
            # in the audio path: conversation_item_added fires once per
            # completed message and function_tools_executed once per resolved
            # tool batch, so this stays a passive observer like the rest of
            # attach(). Capturing per-token or per-frame replay would NOT be,
            # which is why the other three ReplayCapture modalities stay unwired.
            on(
                "conversation_item_added",
                _emit_conversation_item(session_id, session, snapshotter),
            )
            on(
                "function_tools_executed",
                _emit_tools_executed(session_id, session, snapshotter),
            )

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
    transcript: bool = True,
    snapshots: bool = False,
    turns: bool = True,
    dead_air: bool = True,
) -> str:
    """Register a ``VoiceGatewayObserver`` on a Pipecat ``PipelineTask``.

    Mirrors the LiveKit ``attach`` path: builds the same sink, stamps the tenant
    ContextVar, and returns the correlation session id. The observer is the sole
    meter; it finalizes itself on the pipeline ``EndFrame`` (drain + flush).

    ``transcript``, ``snapshots`` and ``turns`` are accepted for signature parity
    with the LiveKit path but are not captured on Pipecat yet. A Pipecat
    transcript would come from transcription frames, and snapshots would need the
    equivalent of LiveKit's ``conversation_item_added`` /
    ``function_tools_executed``; both are separate hooks. They are no-ops here
    rather than errors, so the same ``attach(...)`` call works against either
    framework.

    ``turns`` and ``dead_air`` both need Pipecat speech-boundary events
    equivalent to LiveKit's user/agent started/stopped, which the observer does
    not surface today. Accepting them silently is the deliberate choice over
    raising, but it does mean a Pipecat agent gets no turn rows and no dead-air
    events, so ``e2e_ms`` stays null there and the dead-air view stays empty.
    """
    _ = transcript  # reserved: Pipecat transcript capture is a future step
    _ = snapshots  # reserved: needs a Pipecat message/tool-call hook
    _ = turns  # reserved: needs Pipecat speech-boundary events
    _ = dead_air  # reserved: same missing events as turns above
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
            # A Pipecat worker is not on a LiveKit job, so it has no dispatch name
            # and cannot be probed by one. Explicit None keeps it in the roster but
            # off the probe path, rather than defaulting to the agent-id label.
            dispatch_name=None,
        )

    return session_id


__all__ = [
    "attach",
    "attach_session",
    "register_components",
    "reset_components",
]
