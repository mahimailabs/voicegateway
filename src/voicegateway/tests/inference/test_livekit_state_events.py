"""Turn capture driven by the events LiveKit actually emits.

The four discrete speech events this code bound for years
(``user_started_speaking``, ``user_stopped_speaking``,
``agent_started_speaking``, ``agent_stopped_speaking``) are absent from
``EventTypes`` in livekit-agents 1.5.7 and 1.6.9 alike, and the names appear
nowhere in the package on either version. Nothing in the supported
``>=1.5,<1.7`` range has ever emitted them, so the handlers were never invoked
and the ``turns`` table stayed empty.

Every existing test drove those discrete names against a permissive double, so
they proved the handlers work and never that LiveKit calls them. These drive the
real event set instead, which is the only thing that distinguishes a working
build from the one that shipped.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
from typing import Any

import pytest

import voicegateway
from voicegateway.inference.session.context import reset_session_id
from voicegateway.services.sinks import LocalSqliteSink
from voicegateway.services.storage_service import StorageService

attach_mod = importlib.import_module("voicegateway.inference.session.attach")

# The exact set a 1.5/1.6 AgentSession can emit for speech boundaries.
_LIVEKIT_SPEECH_EVENTS = ("user_state_changed", "agent_state_changed")


class _StrictSession:
    """AgentSession double that refuses events LiveKit does not define.

    A permissive double is what let this bug survive: it accepted
    ``user_started_speaking`` happily, so the tests passed while production
    never fired a single handler. This one only accepts names present in the
    installed ``livekit.agents`` ``EventTypes``, so a test cannot rely on an
    event the library will not send.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Any]] = {}
        self.stt = None
        self.llm = None
        self.tts = None
        self.current_agent = None

    def on(self, event_name: str, handler: Any) -> None:
        if inspect.iscoroutinefunction(handler):
            raise ValueError("Cannot register an async callback with `.on()`.")
        self._handlers.setdefault(event_name, []).append(handler)

    def emit(self, event_name: str, event: Any) -> None:
        if event_name not in _live_event_types():
            raise AssertionError(
                f"{event_name!r} is not in livekit's EventTypes; a test driving "
                "it proves nothing about a real AgentSession"
            )
        for handler in list(self._handlers.get(event_name, [])):
            handler(event)


def _live_event_types() -> frozenset[str]:
    from livekit.agents.voice.events import EventTypes

    return frozenset(EventTypes.__args__)  # type: ignore[attr-defined]


class _UserState:
    """``UserStateChangedEvent``: old_state/new_state over speaking|listening|away."""

    def __init__(self, old_state: str, new_state: str) -> None:
        self.old_state = old_state
        self.new_state = new_state
        self.created_at = 0.0


class _AgentState:
    """``AgentStateChangedEvent``: adds initializing|idle|thinking."""

    def __init__(self, old_state: str, new_state: str) -> None:
        self.old_state = old_state
        self.new_state = new_state
        self.created_at = 0.0


async def _drain() -> None:
    """Settle the tasks the sync handlers scheduled."""
    loop = asyncio.get_running_loop()

    def _pending() -> list[Any]:
        return [
            t
            for t in [*attach_mod._turn_tasks, *attach_mod._close_tasks]
            if not t.done() and t.get_loop() is loop
        ]

    for _ in range(100):
        pending = _pending()
        if not pending:
            await asyncio.sleep(0)
            if not _pending():
                return
            continue
        await asyncio.gather(*pending, return_exceptions=True)
    raise AssertionError("turn event tasks never settled")


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)
    monkeypatch.delenv("VOICEGW_DB_URL", raising=False)
    reset_session_id()
    yield
    reset_session_id()


def _sink(tmp_path: Any) -> LocalSqliteSink:
    return LocalSqliteSink(StorageService(str(tmp_path / "state.db")))


async def _stored_turns(sink: LocalSqliteSink, session_id: str) -> list[Any]:
    from voicegateway.repository import turns_repository as turns

    storage = sink._storage
    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        return await turns.list_turns_by_session(db, session_id)


# --- the library contract ------------------------------------------------


def test_livekit_does_not_define_the_discrete_speech_events() -> None:
    """Pins the reason this fix exists.

    If a future LiveKit reintroduces them, this fails and whoever sees it can
    simplify the bindings on purpose rather than discovering the overlap by
    accident.
    """
    defined = _live_event_types()
    for name in (
        "user_started_speaking",
        "user_stopped_speaking",
        "agent_started_speaking",
        "agent_stopped_speaking",
    ):
        assert name not in defined, (
            f"{name} is now a real LiveKit event; the discrete bindings in "
            "_bind_turn_events are no longer dead weight and the dual-binding "
            "comment needs revisiting"
        )


def test_the_state_events_we_depend_on_exist() -> None:
    """The other half: a rename upstream must break loudly, not silently."""
    defined = _live_event_types()
    for name in _LIVEKIT_SPEECH_EVENTS:
        assert name in defined, f"{name} disappeared from livekit's EventTypes"


# --- the fix -------------------------------------------------------------


async def test_a_full_turn_from_only_the_real_event_set(tmp_path) -> None:
    """The failing case: nothing but 1.6 events, and a complete turn out.

    The strict double refuses the discrete names, so this cannot pass by
    accidentally exercising the legacy path.
    """
    sink = _sink(tmp_path)
    session = _StrictSession()
    sid = voicegateway.attach(session, project="p", sink=sink)

    session.emit("user_state_changed", _UserState("listening", "speaking"))
    await _drain()
    session.emit("user_state_changed", _UserState("speaking", "listening"))
    await _drain()
    session.emit("agent_state_changed", _AgentState("thinking", "speaking"))
    await _drain()
    session.emit("agent_state_changed", _AgentState("speaking", "listening"))
    await _drain()
    session.emit("close", _UserState("listening", "listening"))
    await _drain()

    rows = await _stored_turns(sink, sid)
    assert len(rows) == 1, f"expected one turn from the real event set, got {rows}"
    turn = rows[0]
    assert turn.turn_index == 0
    assert turn.caller_speak_start_ms is not None
    assert turn.caller_speak_end_ms is not None
    assert turn.agent_speak_start_ms is not None, "no agent start: the turn never closed"
    assert turn.agent_speak_end_ms is not None
    assert turn.response_speed_ms is not None
    assert turn.response_speed_ms >= 0


async def test_the_agent_start_is_what_closes_the_turn(tmp_path) -> None:
    """Without the agent_state_changed bridge the table is empty, not partial.

    A turn is buffered by on_agent_audio_first_frame, so a run with caller
    boundaries but no agent start must produce nothing at all. That is exactly
    the reported symptom.
    """
    sink = _sink(tmp_path)
    session = _StrictSession()
    sid = voicegateway.attach(session, project="p", sink=sink)

    session.emit("user_state_changed", _UserState("listening", "speaking"))
    await _drain()
    session.emit("user_state_changed", _UserState("speaking", "listening"))
    await _drain()

    assert await _stored_turns(sink, sid) == []


async def test_speaking_to_away_still_counts_as_a_stop(tmp_path) -> None:
    """Guarding on the destination would drop this; guarding on origin does not.

    The caller can go speaking -> away without passing through listening, and
    that is still them finishing an utterance.
    """
    sink = _sink(tmp_path)
    session = _StrictSession()
    sid = voicegateway.attach(session, project="p", sink=sink)

    session.emit("user_state_changed", _UserState("listening", "speaking"))
    await _drain()
    session.emit("user_state_changed", _UserState("speaking", "away"))
    await _drain()
    session.emit("agent_state_changed", _AgentState("thinking", "speaking"))
    await _drain()
    session.emit("close", _UserState("away", "away"))
    await _drain()

    rows = await _stored_turns(sink, sid)
    assert len(rows) == 1
    assert rows[0].response_speed_ms is not None, (
        "the speaking -> away transition did not record the caller's stop, so "
        "response_speed_ms was measured from the wrong instant"
    )


async def test_an_away_transition_that_is_not_a_stop_opens_nothing(tmp_path) -> None:
    """listening -> away is not speech ending; no turn should exist."""
    sink = _sink(tmp_path)
    session = _StrictSession()
    sid = voicegateway.attach(session, project="p", sink=sink)

    session.emit("user_state_changed", _UserState("listening", "away"))
    await _drain()
    session.emit("agent_state_changed", _AgentState("idle", "listening"))
    await _drain()
    session.emit("close", _UserState("away", "away"))
    await _drain()

    assert await _stored_turns(sink, sid) == []


async def test_agent_thinking_is_not_an_agent_boundary(tmp_path) -> None:
    """Only transitions into and out of ``speaking`` are boundaries.

    ``AgentState`` has initializing/idle/listening/thinking too, and treating
    any of them as a start would close turns that never had agent audio.
    """
    sink = _sink(tmp_path)
    session = _StrictSession()
    sid = voicegateway.attach(session, project="p", sink=sink)

    session.emit("user_state_changed", _UserState("listening", "speaking"))
    await _drain()
    session.emit("user_state_changed", _UserState("speaking", "listening"))
    await _drain()
    session.emit("agent_state_changed", _AgentState("listening", "thinking"))
    await _drain()

    assert await _stored_turns(sink, sid) == [], "thinking closed a turn"


async def test_two_turns_in_one_session(tmp_path) -> None:
    """turn_index has to advance across a real back-and-forth."""
    sink = _sink(tmp_path)
    session = _StrictSession()
    sid = voicegateway.attach(session, project="p", sink=sink)

    for _ in range(2):
        session.emit("user_state_changed", _UserState("listening", "speaking"))
        await _drain()
        session.emit("user_state_changed", _UserState("speaking", "listening"))
        await _drain()
        session.emit("agent_state_changed", _AgentState("thinking", "speaking"))
        await _drain()
        session.emit("agent_state_changed", _AgentState("speaking", "listening"))
        await _drain()

    session.emit("close", _UserState("listening", "listening"))
    await _drain()

    rows = await _stored_turns(sink, sid)
    assert [r.turn_index for r in rows] == [0, 1]


async def test_the_dead_air_clock_follows_the_state_events(tmp_path) -> None:
    """Dead air rides the same bridges, so it must work on the real set too."""
    sink = _sink(tmp_path)
    session = _StrictSession()
    voicegateway.attach(session, project="p", sink=sink)
    activity = session._vg_activity
    assert activity is not None

    session.emit("user_state_changed", _UserState("listening", "speaking"))
    live = activity.probe("x")
    assert live is not None
    assert attach_mod._SpeechActivity._now_ms() - live < 50

    session.emit("user_state_changed", _UserState("speaking", "listening"))
    pinned = activity.probe("x")
    assert activity.probe("x") == pinned, "clock still reports active speech"
