"""``attach(turns=True)``: the write path from a speech event to a stored turn.

Before this existed, ``turns`` had five readers and no writer. ``create_turn``
and ``create_turns_bulk`` had no non-test callers, ``TurnTracker`` was built
only in tests, and its default flush was a no-op that logged "turns dropped (no
repository wired)" at debug. So ``e2e_ms`` and ``turns`` on
``/v1/rooms/{room}/latency``, ``/api/sessions/{id}/turns``, and the five
session-aggregate columns behind the Conversation tab all read empty, with
nothing anywhere reporting a fault.

These tests walk the whole path rather than asserting on the tracker in
isolation, because every previous test did the latter and the gap was between
the parts.
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


class _Session:
    """AgentSession double that mirrors ``livekit.rtc.EventEmitter``.

    ``on`` refuses coroutine functions and ``emit`` is synchronous, exactly as
    the real emitter behaves. A permissive double is what hid the original bug,
    so this one is deliberately strict: registering an async handler raises
    here just as it would in production.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Any]] = {}
        self.stt = None
        self.llm = None
        self.tts = None
        self.current_agent = None

    def on(self, event_name: str, handler: Any) -> None:
        if inspect.iscoroutinefunction(handler):
            raise ValueError(
                "Cannot register an async callback with `.on()`. Use "
                "`asyncio.create_task` within your synchronous callback instead."
            )
        self._handlers.setdefault(event_name, []).append(handler)

    def emit(self, event_name: str, event: Any = None) -> None:
        # LiveKit always emits with an event payload, and its handlers are
        # written to take one, so supply a default rather than calling with no
        # arguments (which no real emit ever does).
        payload = event if event is not None else _Event()
        for handler in list(self._handlers.get(event_name, [])):
            handler(payload)


class _Event:
    """A payload-free event. ``new_state`` is None so the 1.6 onset filter,
    which only reacts to ``speaking``, correctly ignores it."""

    new_state = None
    created_at = 0.0


class _StateChanged(_Event):
    """The LiveKit 1.6 onset event, which carries the new state."""

    def __init__(self, new_state: str) -> None:
        self.new_state = new_state


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
def _isolated(monkeypatch):
    """Give each test its own database AND its own session id.

    Both are shared state that made these tests pass alone and fail
    intermittently in the full suite:

    - ``resolve_database_url`` gives ``VOICEGW_DB_PATH`` precedence over the
      path ``StorageService`` was constructed with, so a leftover env var sends
      every test's writes into one file.
    - ``get_or_create_session_id`` is a ContextVar that outlives a test, so
      without a reset every test in this module attaches under the same id.

    Together those two mean test N reads test N-1's turns and counts two.
    """
    monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)
    monkeypatch.delenv("VOICEGW_DB_URL", raising=False)
    reset_session_id()
    yield
    reset_session_id()


def _sink(tmp_path: Any) -> LocalSqliteSink:
    return LocalSqliteSink(StorageService(str(tmp_path / "turns.db")))


async def _stored_turns(sink: LocalSqliteSink, session_id: str) -> list[Any]:
    from voicegateway.repository import turns_repository as turns

    storage = sink._storage
    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        return await turns.list_turns_by_session(db, session_id)


async def test_a_turn_reaches_storage(tmp_path) -> None:
    """The whole point: speech events in, a row in the turns table out."""
    sink = _sink(tmp_path)
    session = _Session()

    sid = voicegateway.attach(session, project="p", sink=sink)

    session.emit("user_started_speaking")
    await _drain()
    session.emit("user_stopped_speaking")
    await _drain()
    session.emit("agent_started_speaking")
    await _drain()
    session.emit("agent_stopped_speaking")
    await _drain()

    # close_session emits the tail and flushes below the flush-size threshold.
    capture = session._vg_capture
    assert capture is not None
    session.emit("close")
    await _drain()

    rows = await _stored_turns(sink, sid)
    assert len(rows) == 1, f"expected one stored turn, got {rows}"
    turn = rows[0]
    assert turn.session_id == sid
    assert turn.turn_index == 0
    assert turn.agent_speak_start_ms is not None
    assert turn.agent_speak_end_ms is not None
    assert turn.response_speed_ms is not None
    assert turn.response_speed_ms >= 0


async def test_livekit_16_onset_starts_a_turn(tmp_path) -> None:
    """1.6 emits no discrete user_started_speaking; the turn must still start.

    LiveKit 1.6 signals onset as ``user_state_changed`` -> ``speaking``.
    ``capture.py`` already hooked both for first-partial latency; turn capture
    did not, so on 1.6 every turn start went uncaptured even with a tracker
    bound. Only the 1.6 event is emitted here, so a regression to
    ``user_started_speaking`` alone fails this.
    """
    sink = _sink(tmp_path)
    session = _Session()
    sid = voicegateway.attach(session, project="p", sink=sink)

    session.emit("user_state_changed", _StateChanged("speaking"))
    await _drain()
    session.emit("user_stopped_speaking")
    await _drain()
    session.emit("agent_started_speaking")
    await _drain()
    session.emit("close")
    await _drain()

    rows = await _stored_turns(sink, sid)
    assert len(rows) == 1, "the 1.6 onset event did not start a turn"


async def test_non_speaking_state_changes_do_not_start_a_turn(tmp_path) -> None:
    """``user_state_changed`` also fires for listening/away."""
    sink = _sink(tmp_path)
    session = _Session()
    sid = voicegateway.attach(session, project="p", sink=sink)

    session.emit("user_state_changed", _StateChanged("listening"))
    await _drain()
    session.emit("user_state_changed", _StateChanged("away"))
    await _drain()
    session.emit("agent_started_speaking")
    await _drain()
    session.emit("close")
    await _drain()

    assert await _stored_turns(sink, sid) == []


async def test_both_onset_events_produce_one_turn(tmp_path) -> None:
    """A build emitting both must not double-count the turn start.

    First-wins is TurnTracker's own guard (it only sets the pending start when
    it is None), so this pins the behaviour the dual hook depends on.
    """
    sink = _sink(tmp_path)
    session = _Session()
    sid = voicegateway.attach(session, project="p", sink=sink)

    session.emit("user_state_changed", _StateChanged("speaking"))
    session.emit("user_started_speaking")
    await _drain()
    session.emit("user_stopped_speaking")
    await _drain()
    session.emit("agent_started_speaking")
    await _drain()
    session.emit("close")
    await _drain()

    rows = await _stored_turns(sink, sid)
    assert len(rows) == 1, f"onset fired twice produced {len(rows)} turns"


async def test_turns_false_writes_nothing(tmp_path) -> None:
    """The opt-out has to actually opt out."""
    sink = _sink(tmp_path)
    session = _Session()
    sid = voicegateway.attach(session, project="p", sink=sink, turns=False)

    session.emit("user_started_speaking")
    await _drain()
    session.emit("agent_started_speaking")
    await _drain()
    session.emit("close")
    await _drain()

    assert await _stored_turns(sink, sid) == []


async def test_env_kill_switch_beats_the_argument(tmp_path, monkeypatch) -> None:
    """``VOICEGW_TURNS=0`` turns it off fleet-wide, over an explicit True."""
    monkeypatch.setenv("VOICEGW_TURNS", "0")
    sink = _sink(tmp_path)
    session = _Session()
    sid = voicegateway.attach(session, project="p", sink=sink, turns=True)

    session.emit("user_started_speaking")
    await _drain()
    session.emit("agent_started_speaking")
    await _drain()
    session.emit("close")
    await _drain()

    assert await _stored_turns(sink, sid) == []


async def test_handlers_are_not_coroutine_functions(tmp_path) -> None:
    """The regression guard for the bug that made this unusable in production.

    ``EventEmitter.on`` raises ValueError on a coroutine function, so an async
    handler is not a slow path, it is a crash on any real AgentSession. The
    strict double above already enforces this during attach; this states it
    directly so the reason survives a refactor of the double.
    """
    # import_module, not ``from ... import attach``: the package __init__ binds
    # ``attach`` to the FUNCTION, so the plain import would shadow the module.
    attach_mod = importlib.import_module("voicegateway.inference.session.attach")
    from voicegateway.middleware.turn_tracker_middleware import TurnTracker

    tracker = TurnTracker()
    for factory in (
        attach_mod._emit_user_started,
        attach_mod._emit_user_stopped,
        attach_mod._emit_agent_started,
        attach_mod._emit_agent_stopped,
    ):
        handler = factory("s", tracker)
        assert not inspect.iscoroutinefunction(handler), (
            f"{factory.__name__} returned a coroutine function; "
            "LiveKit's EventEmitter.on rejects those outright"
        )
