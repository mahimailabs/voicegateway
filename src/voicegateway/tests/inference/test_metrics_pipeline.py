"""Integration test for the v0.2.0 metrics-capture pipeline."""

from __future__ import annotations

import asyncio
import importlib
import inspect
from typing import Any

import pytest

from voicegateway.inference import attach_session
from voicegateway.inference.session.attach import reset_components
from voicegateway.middleware.dead_air_detector_middleware import (
    DeadAirDetector,
    DeadAirEvent,
)
from voicegateway.middleware.turn_tracker_middleware import TurnRow, TurnTracker


class FakeAgentSession:
    """Minimal EventEmitter double for livekit-agents AgentSession.

    Mirrors ``livekit.rtc.EventEmitter`` on the two points that matter:
    ``emit`` is SYNCHRONOUS, and ``on`` REFUSES a coroutine function with the
    same ValueError the real one raises.

    This double previously did neither: it was ``async def emit`` awaiting each
    handler. That is not a contract LiveKit has ever offered, and modelling it
    is what let ``attach_session`` register ``async def`` handlers for years.
    Against a real ``AgentSession`` those calls raise:

        ValueError: Cannot register an async callback with `.on()`.
        Use `asyncio.create_task` within your synchronous callback instead.

    So turn capture could not have worked in production, and no test could see
    it. Keep this faithful, or the same class of bug walks straight back in.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Any]] = {}

    def on(self, event_name: str, handler: Any) -> None:
        if inspect.iscoroutinefunction(handler):
            raise ValueError(
                "Cannot register an async callback with `.on()`. Use "
                "`asyncio.create_task` within your synchronous callback instead."
            )
        self._handlers.setdefault(event_name, []).append(handler)

    def emit(self, event_name: str, *args: Any) -> None:
        for h in self._handlers.get(event_name, []):
            h(*args)


async def _drain() -> None:
    """Wait for the tasks the sync handlers scheduled, deterministically.

    Handlers are sync and schedule their await via ``asyncio.create_task``, so
    the effect lands later. Counting event-loop passes is a race: it passed in
    isolation and failed intermittently under the full suite, where the tracker
    contends for its lock. ``attach._turn_tasks`` holds a strong ref to every
    in-flight task, so awaiting that set settles on the actual work rather than
    on a guess, and raises instead of under-draining silently.
    """
    attach_mod = importlib.import_module("voicegateway.inference.session.attach")
    loop = asyncio.get_running_loop()

    def _pending() -> list[Any]:
        # BOTH task sets. The per-event handlers land in ``_turn_tasks``, but
        # attach()'s close path schedules ``_finish`` into ``_close_tasks``, and
        # that is what calls ``turn_tracker.close_session`` and flushes the
        # buffer. Watching only ``_turn_tasks`` returned before the flush, which
        # is what made these tests pass alone and fail under the full suite.
        #
        # Filtered to this test's loop: both sets are module-global and pytest
        # gives each test its own loop, so they can still hold tasks created on
        # an earlier, now-closed loop; awaiting one of those never returns.
        return [
            t
            for t in [*attach_mod._turn_tasks, *attach_mod._close_tasks]
            if not t.done() and t.get_loop() is loop
        ]

    for _ in range(100):
        pending = _pending()
        if not pending:
            # One more pass so a task that just finished can schedule follow-on
            # work (close_session flushes through the sink).
            await asyncio.sleep(0)
            if not _pending():
                return
            continue
        await asyncio.gather(*pending, return_exceptions=True)
    raise AssertionError("turn event tasks never settled")


@pytest.fixture(autouse=True)
def _clean_registry():
    """Ensure each test starts with no registered components."""
    reset_components()
    yield
    reset_components()


async def test_attach_session_routes_full_turn_to_tracker() -> None:
    """End-to-end: events fire → TurnTracker captures → close_session flushes."""
    captured: list[list[TurnRow]] = []

    async def flush_callback(rows: list[TurnRow]) -> None:
        captured.append(rows)

    tracker = TurnTracker(flush_callback=flush_callback, flush_size=100)
    agent = FakeAgentSession()

    sid = attach_session(agent, session_id="test-session", turn_tracker=tracker)
    assert sid == "test-session"

    # Drive a normal turn lifecycle.
    agent.emit("user_started_speaking")
    await _drain()
    agent.emit("user_stopped_speaking")
    await _drain()
    agent.emit("agent_started_speaking")
    await _drain()
    agent.emit("agent_stopped_speaking")
    await _drain()
    agent.emit("close")
    await _drain()

    # `close` fires tracker.close_session(sid) which flushes the buffer.
    assert len(captured) == 1
    flushed = captured[0]
    assert len(flushed) == 1
    turn = flushed[0]
    assert turn.session_id == "test-session"
    assert turn.turn_index == 0
    assert turn.agent_speak_start_ms is not None
    assert turn.agent_speak_end_ms is not None
    assert turn.response_speed_ms is not None
    assert turn.response_speed_ms >= 0


async def test_attach_session_no_tracker_no_op_warning() -> None:
    """When no TurnTracker is registered, attach_session logs and returns the sid."""
    agent = FakeAgentSession()
    sid = attach_session(agent, session_id="ghost-session")
    assert sid == "ghost-session"


async def test_attach_session_starts_dead_air_detector() -> None:
    """A registered DeadAirDetector gets ``start(sid)`` called on attach."""
    started: list[str] = []
    stopped: list[str] = []

    class _Spy(DeadAirDetector):
        async def start(self, session_id: str) -> None:
            started.append(session_id)

        async def stop(self, session_id: str) -> None:
            stopped.append(session_id)

    async def _probe(_: str) -> int | None:
        return None

    spy_detector = _Spy(activity_probe=_probe)

    tracker = TurnTracker()
    agent = FakeAgentSession()
    attach_session(
        agent,
        session_id="probe-session",
        turn_tracker=tracker,
        dead_air_detector=spy_detector,
    )

    # Allow the spawned start() task to run.
    await asyncio.sleep(0)
    assert started == ["probe-session"]

    agent.emit("close")
    await _drain()
    assert stopped == ["probe-session"]


async def test_attach_session_close_calls_cost_tracker() -> None:
    """A registered CostTracker gets ``close_session(sid)`` on close event."""
    closed: list[str] = []

    class _CT:
        async def close_session(self, session_id: str) -> None:
            closed.append(session_id)

    tracker = TurnTracker()
    agent = FakeAgentSession()
    attach_session(
        agent,
        session_id="cost-session",
        turn_tracker=tracker,
        cost_tracker=_CT(),  # type: ignore[arg-type]
    )

    agent.emit("close")
    await _drain()
    assert closed == ["cost-session"]


async def test_dead_air_event_dataclass_round_trips() -> None:
    """Smoke: DeadAirEvent is a usable, comparable dataclass for the pipeline."""
    e1 = DeadAirEvent(
        session_id="s",
        started_at_ms=1000,
        duration_ms=3500,
        threshold_used_ms=3000,
    )
    e2 = DeadAirEvent(
        session_id="s",
        started_at_ms=1000,
        duration_ms=3500,
        threshold_used_ms=3000,
    )
    assert e1 == e2
