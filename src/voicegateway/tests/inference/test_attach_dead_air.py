"""``attach(dead_air=True)``: the write path from silence to a stored event.

``DeadAirDetector`` was constructed nowhere outside tests, so ``on_event`` was
always the no-op that logs "dropped (no repository wired)" at debug,
``dead_air_events`` was always empty, and ``GET /api/sessions/{id}/dead_air``
always returned nothing.

The activity probe is the part worth testing hardest. A probe that only recorded
discrete speech events would report the onset timestamp for a whole utterance,
so a caller talking for longer than the threshold would trip a dead-air event
mid-sentence. That is a false alert somebody acts on, which is worse than the
missing metric this fixes.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
from typing import Any

import pytest

import voicegateway
from voicegateway.inference.session.context import reset_session_id
from voicegateway.middleware.dead_air_detector_middleware import (
    DeadAirDetector,
    DeadAirEvent,
)
from voicegateway.services.sinks import LocalSqliteSink
from voicegateway.services.storage_service import StorageService

attach_mod = importlib.import_module("voicegateway.inference.session.attach")


class _Session:
    """AgentSession double mirroring ``livekit.rtc.EventEmitter``."""

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
        payload = event if event is not None else _Event()
        for handler in list(self._handlers.get(event_name, [])):
            handler(payload)


class _Event:
    new_state = None
    created_at = 0.0


class _StateChanged(_Event):
    def __init__(self, new_state: str) -> None:
        self.new_state = new_state


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """Own database and own session id, as for the turn tests."""
    monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)
    monkeypatch.delenv("VOICEGW_DB_URL", raising=False)
    reset_session_id()
    yield
    reset_session_id()


def _sink(tmp_path: Any) -> LocalSqliteSink:
    return LocalSqliteSink(StorageService(str(tmp_path / "dead_air.db")))


async def _stored_events(sink: LocalSqliteSink, session_id: str) -> list[Any]:
    from voicegateway.repository import dead_air_repository as dead_air

    storage = sink._storage
    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        return await dead_air.list_events_by_session(db, session_id)


# --- the activity probe --------------------------------------------------


def test_silence_ages_but_active_speech_does_not() -> None:
    """The invariant the whole feature rests on.

    While anyone is speaking the probe must report *now*, so the computed
    silence stays at zero no matter how long the utterance runs. Once they
    stop, the clock starts ageing from the stop.
    """
    activity = attach_mod._SpeechActivity()

    activity.caller_started()
    first = activity.probe("s")
    # Simulate the detector polling twice during one long utterance.
    second = activity.probe("s")
    assert first is not None and second is not None
    assert second >= first, "the probe went backwards while speech was active"

    now = attach_mod._SpeechActivity._now_ms()
    assert now - second < 50, (
        "probe reported a stale timestamp while the caller was still speaking; "
        "a long utterance would trip a false dead-air event"
    )

    activity.caller_stopped()
    stopped_at = activity.probe("s")
    assert stopped_at is not None
    # After the stop the value is pinned, so silence accumulates against it.
    assert activity.probe("s") == stopped_at


def test_agent_speech_also_counts_as_activity() -> None:
    activity = attach_mod._SpeechActivity()
    activity.agent_started()
    pinned = activity.probe("s")
    assert pinned is not None
    assert attach_mod._SpeechActivity._now_ms() - pinned < 50

    activity.agent_stopped()
    assert activity.probe("s") == activity.probe("s")


def test_a_duplicated_onset_does_not_wedge_the_clock() -> None:
    """Booleans, not a counter, and this is why.

    LiveKit 1.6 signals onset through ``user_state_changed`` while older builds
    emit the discrete event. A build emitting both would leave a counter
    permanently above zero, and the probe would report "speaking" forever, so
    dead air could never fire again for that session.
    """
    activity = attach_mod._SpeechActivity()
    activity.caller_started()
    activity.caller_started()  # the duplicate
    activity.caller_stopped()

    pinned = activity.probe("s")
    assert pinned is not None
    assert activity.probe("s") == pinned, "clock still reports active speech"


def test_overlapping_speakers_both_have_to_stop() -> None:
    """Talk-over: the caller stopping does not mean the line went quiet."""
    activity = attach_mod._SpeechActivity()
    activity.caller_started()
    activity.agent_started()
    activity.caller_stopped()

    live = activity.probe("s")
    assert live is not None
    assert attach_mod._SpeechActivity._now_ms() - live < 50, (
        "clock froze while the agent was still speaking"
    )

    activity.agent_stopped()
    pinned = activity.probe("s")
    assert activity.probe("s") == pinned


# --- the wiring ----------------------------------------------------------


async def test_a_dead_air_event_reaches_storage(tmp_path) -> None:
    """The whole point: an observed silence becomes a stored row.

    Driven through a real DeadAirDetector against the sink-backed on_event that
    ``attach`` builds, with a short threshold so the test does not sleep for
    the 3s default.
    """
    sink = _sink(tmp_path)
    # attach() warms the store before starting its watcher; this drives the
    # detector directly, so it has to do the same. Without it the first write
    # runs migrations from inside the poll loop and can lose the event to
    # "database is locked".
    await sink._storage._ensure_initialized()
    detector, activity = attach_mod._build_dead_air_detector(sink, None)

    # Rebuild at a test-sized threshold; the callback is what is under test.
    fast = DeadAirDetector(
        activity_probe=activity.probe,
        on_event=detector._on_event,
        threshold_seconds=0.05,
        poll_interval_seconds=0.01,
    )

    activity.caller_started()
    activity.caller_stopped()
    await fast.start("sess-dead")
    try:
        # Poll to a generous deadline rather than sleeping a fixed slice. The
        # watcher needs ~6 polls to cross the threshold, and a fixed sleep that
        # is merely usually long enough is how a suite gets a flaky test.
        rows: list[Any] = []
        deadline = asyncio.get_running_loop().time() + 10.0
        while not rows:
            if asyncio.get_running_loop().time() > deadline:
                break
            await asyncio.sleep(0.02)
            rows = await _stored_events(sink, "sess-dead")
    finally:
        await fast.stop("sess-dead")

    assert len(rows) >= 1, "an observed silence was not persisted"
    assert rows[0].session_id == "sess-dead"
    assert rows[0].duration_ms >= 50


async def test_no_event_while_speech_continues(tmp_path) -> None:
    """The false-positive guard, end to end.

    The caller never stops speaking for longer than the threshold, so nothing
    should fire, however long the watcher runs.
    """
    sink = _sink(tmp_path)
    await sink._storage._ensure_initialized()
    _, activity = attach_mod._build_dead_air_detector(sink, None)

    async def _on_event(event: DeadAirEvent) -> None:
        await sink.log_dead_air([event])

    fast = DeadAirDetector(
        activity_probe=activity.probe,
        on_event=_on_event,
        threshold_seconds=0.05,
        poll_interval_seconds=0.01,
    )

    activity.caller_started()  # and never stops
    await fast.start("sess-talking")
    await asyncio.sleep(0.25)
    await fast.stop("sess-talking")

    assert await _stored_events(sink, "sess-talking") == [], (
        "fired a dead-air event while the caller was still speaking"
    )


async def test_attach_binds_the_activity_clock(tmp_path) -> None:
    """The four speech events have to actually drive the clock."""
    sink = _sink(tmp_path)
    session = _Session()
    voicegateway.attach(session, project="p", sink=sink)

    detector = session._vg_dead_air
    assert detector is not None, "attach did not build a detector"
    activity = session._vg_activity
    assert activity is not None

    session.emit("user_started_speaking")
    live = activity.probe("x")
    assert live is not None
    assert attach_mod._SpeechActivity._now_ms() - live < 50

    session.emit("user_stopped_speaking")
    pinned = activity.probe("x")
    assert activity.probe("x") == pinned


async def test_the_16_listening_transition_ends_speech(tmp_path) -> None:
    """On a build with no discrete stop event, ``listening`` is the only signal.

    Without this the clock would stay "speaking" for the rest of the call and
    dead air could never fire.
    """
    sink = _sink(tmp_path)
    session = _Session()
    voicegateway.attach(session, project="p", sink=sink)
    activity = session._vg_activity

    session.emit("user_state_changed", _StateChanged("speaking"))
    session.emit("user_state_changed", _StateChanged("listening"))

    pinned = activity.probe("x")
    assert activity.probe("x") == pinned, (
        "the listening transition did not end the utterance"
    )


async def test_dead_air_false_builds_no_detector(tmp_path) -> None:
    sink = _sink(tmp_path)
    session = _Session()
    voicegateway.attach(session, project="p", sink=sink, dead_air=False)
    assert getattr(session, "_vg_dead_air", None) is None


async def test_env_kill_switch_beats_the_argument(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VOICEGW_DEAD_AIR", "0")
    sink = _sink(tmp_path)
    session = _Session()
    voicegateway.attach(session, project="p", sink=sink, dead_air=True)
    assert getattr(session, "_vg_dead_air", None) is None


async def test_turns_off_still_gives_dead_air(tmp_path) -> None:
    """The two flags are independent, which is the reason dead air has its own.

    Turn capture off, dead air on: the events must still be bound, because the
    activity clock rides them.
    """
    sink = _sink(tmp_path)
    session = _Session()
    voicegateway.attach(session, project="p", sink=sink, turns=False, dead_air=True)

    activity = session._vg_activity
    assert activity is not None
    session.emit("user_started_speaking")
    live = activity.probe("x")
    assert live is not None
    assert attach_mod._SpeechActivity._now_ms() - live < 50


async def test_attach_warms_the_store_before_watching(tmp_path) -> None:
    """The watcher must not be the thing that triggers migrations.

    The detector latches ``_already_fired`` before awaiting its callback, so a
    "database is locked" on that first write drops the event permanently rather
    than retrying on the next poll. attach() therefore initialises the store as
    part of starting the watcher.
    """
    sink = _sink(tmp_path)
    storage = sink._storage
    assert storage._initialized is False

    session = _Session()
    voicegateway.attach(session, project="p", sink=sink)

    detector = session._vg_dead_air
    assert detector is not None
    try:
        # Real time, not loop passes: the warm-up runs alembic, which hops
        # through a thread pool, so yielding N times proves nothing.
        deadline = asyncio.get_running_loop().time() + 10.0
        while not storage._initialized:
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError(
                    "attach started the dead-air watcher without warming the store"
                )
            await asyncio.sleep(0.01)
    finally:
        for sid in list(detector.active_sessions()):
            await detector.stop(sid)
