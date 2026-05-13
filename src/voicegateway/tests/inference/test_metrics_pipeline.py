"""Integration test for the v0.2.0 metrics-capture pipeline."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from voicegateway.inference import attach_session
from voicegateway.inference.session.attach import reset_components
from voicegateway.middleware.dead_air_detector import DeadAirDetector, DeadAirEvent
from voicegateway.middleware.turn_tracker import TurnRow, TurnTracker


class FakeAgentSession:
    """Minimal EventEmitter doubling for livekit-agents AgentSession."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Any]] = {}

    def on(self, event_name: str, handler: Any) -> None:
        self._handlers.setdefault(event_name, []).append(handler)

    async def emit(self, event_name: str) -> None:
        for h in self._handlers.get(event_name, []):
            await h()


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
    await agent.emit("user_started_speaking")
    await agent.emit("user_stopped_speaking")
    await agent.emit("agent_started_speaking")
    await agent.emit("agent_stopped_speaking")
    await agent.emit("close")

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
    # No handlers should have been registered.
    assert agent._handlers == {}


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

    await agent.emit("close")
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

    await agent.emit("close")
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
