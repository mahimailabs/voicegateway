"""``response_speed_ms`` is measured from when the caller actually stopped.

``user_state_changed -> listening`` does not fire when the caller stops. LiveKit
raises it only after voice activity has waited out ``min_silence_duration``
(0.55s with Silero's default), and it backdates the true stop into
``last_speaking_time``, which ``UserStateChangedEvent`` never publishes: the
event carries ``old_state``, ``new_state`` and a ``created_at`` stamped at
delivery.

A stop recorded there is therefore late by the whole silence window, and every
response speed derived from it is short by the same amount. It biases in the
flattering direction, which is why it reads as good latency rather than a bug.

What is recoverable is ``EOUMetrics.end_of_utterance_delay``, which LiveKit
computes as ``max(now - last_speaking_time, 0)``. Subtracting it from the moment
the metric was produced recovers the anchor without hardcoding any VAD setting.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import time
from typing import Any

import pytest

import voicegateway
from voicegateway.inference.session.context import reset_session_id
from voicegateway.middleware.turn_tracker_middleware import TurnRow, TurnTracker
from voicegateway.services.sinks import LocalSqliteSink
from voicegateway.services.storage_service import StorageService

attach_mod = importlib.import_module("voicegateway.inference.session.attach")


class _Session:
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
        for handler in list(self._handlers.get(event_name, [])):
            handler(event)


class _UserState:
    def __init__(self, old_state: str, new_state: str) -> None:
        self.old_state = old_state
        self.new_state = new_state
        self.created_at = 0.0


class _AgentState(_UserState):
    pass


class _EOU:
    """An ``EOUMetrics``, as it reaches ``metrics_collected``.

    ``timestamp`` is ``time.time()`` at emission, matching LiveKit. Note it
    carries no ``stopped_speaking_at``: LiveKit computes one internally
    (``_EndOfTurnMetrics``) but never publishes it, which is why the anchor has
    to be derived rather than read.
    """

    def __init__(self, end_of_utterance_delay: float) -> None:
        self.end_of_utterance_delay = end_of_utterance_delay
        self.transcription_delay = 0.12
        self.timestamp = time.time()


class _MetricsCollected:
    def __init__(self, metrics: Any) -> None:
        self.metrics = metrics


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)
    monkeypatch.delenv("VOICEGW_DB_URL", raising=False)
    reset_session_id()
    yield
    reset_session_id()


def _sink(tmp_path: Any) -> LocalSqliteSink:
    return LocalSqliteSink(StorageService(str(tmp_path / "anchor.db")))


async def _drain() -> None:
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


async def _stored(sink: LocalSqliteSink, session_id: str) -> list[Any]:
    from voicegateway.repository import turns_repository as turns

    storage = sink._storage
    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        return await turns.list_turns_by_session(db, session_id)


async def _run_turn(session: Any, *, eou: float | None) -> None:
    """One caller/agent exchange in the order LiveKit produces it."""
    session.emit("user_state_changed", _UserState("listening", "speaking"))
    await _drain()
    # Fires at stop + silence window, NOT at the stop.
    session.emit("user_state_changed", _UserState("speaking", "listening"))
    await _drain()
    if eou is not None:
        session.emit("metrics_collected", _MetricsCollected(_EOU(eou)))
        await _drain()
    session.emit("agent_state_changed", _AgentState("thinking", "speaking"))
    await _drain()
    session.emit("agent_state_changed", _AgentState("speaking", "listening"))
    await _drain()


# --- the correction ------------------------------------------------------


async def test_the_anchor_comes_from_the_eou_metric(tmp_path) -> None:
    """The headline case, and the ordering trap in one.

    The state change writes a stop first; the EOU metric arrives later and must
    be allowed to replace it. A large delay is used so the corrected number
    cannot be confused with the uncorrected one: measured from the state change
    the gap here is a few milliseconds, measured from the true stop it is at
    least the 0.689s of EOU delay.
    """
    sink = _sink(tmp_path)
    session = _Session()
    sid = voicegateway.attach(session, project="p", sink=sink)

    await _run_turn(session, eou=0.689)
    session.emit("close", _UserState("listening", "listening"))
    await _drain()

    [turn] = await _stored(sink, sid)
    assert turn.response_speed_ms is not None, (
        "the EOU correction never landed; the first-wins guard swallowed it"
    )
    assert turn.response_speed_ms >= 689, (
        f"response_speed_ms is {turn.response_speed_ms}, which is smaller than "
        "the 689ms of end-of-utterance delay it contains. That is the symptom: "
        "the stop was taken from the state change, not the true anchor"
    )


async def test_response_speed_is_never_less_than_the_eou_it_contains(
    tmp_path,
) -> None:
    """The invariant the consumer used to find this.

    ``end_of_utterance_delay`` is the time from the caller's real stop to the
    turn commit, and the agent cannot start speaking before the turn commits.
    So the response speed strictly contains the EOU delay, and any value below
    it is arithmetically impossible for a correctly anchored measurement.
    """
    sink = _sink(tmp_path)
    session = _Session()
    sid = voicegateway.attach(session, project="p", sink=sink)

    eou_seconds = 0.42
    await _run_turn(session, eou=eou_seconds)
    session.emit("close", _UserState("listening", "listening"))
    await _drain()

    [turn] = await _stored(sink, sid)
    assert turn.response_speed_ms is not None
    assert turn.response_speed_ms >= int(eou_seconds * 1000), (
        f"{turn.response_speed_ms}ms < {int(eou_seconds * 1000)}ms of contained "
        "EOU delay"
    )


async def test_without_an_eou_metric_the_speed_is_absent_not_wrong(
    tmp_path,
) -> None:
    """No usable anchor means no number, not a flattering one.

    Reporting the state-change value here would put two populations half a
    second apart into one percentile, and nothing downstream could tell them
    apart. The gap is visible; the mixture is not.
    """
    sink = _sink(tmp_path)
    session = _Session()
    sid = voicegateway.attach(session, project="p", sink=sink)

    await _run_turn(session, eou=None)
    session.emit("close", _UserState("listening", "listening"))
    await _drain()

    [turn] = await _stored(sink, sid)
    assert turn.response_speed_ms is None, (
        "an uncorrected turn reported a response speed anyway"
    )
    # The boundary itself is still recorded: a 0.55s error is noise in talk
    # time, where it is most of the number in a latency headline.
    assert turn.caller_speak_end_ms is not None


async def test_an_unmeasurable_eou_is_not_treated_as_zero(tmp_path) -> None:
    """``0.0`` means "could not measure", not "no delay".

    LiveKit publishes ``end_of_utterance_delay=info.metrics.end_of_turn_delay
    or 0.0`` and returns None for a stale or missing anchor, so zero is the
    unmeasured case far more often than a real one. Correcting by zero would
    re-stamp the biased value and label it precise, which is worse than leaving
    it alone.
    """
    sink = _sink(tmp_path)
    session = _Session()
    sid = voicegateway.attach(session, project="p", sink=sink)

    await _run_turn(session, eou=0.0)
    session.emit("close", _UserState("listening", "listening"))
    await _drain()

    [turn] = await _stored(sink, sid)
    assert turn.response_speed_ms is None, (
        "a zero EOU delay was taken as a real measurement and used to 'correct' "
        "the anchor, which silently keeps the biased value"
    )


async def test_two_turns_each_get_their_own_correction(tmp_path) -> None:
    """The precise flag has to reset with the turn, or turn 2 refuses its own."""
    sink = _sink(tmp_path)
    session = _Session()
    sid = voicegateway.attach(session, project="p", sink=sink)

    await _run_turn(session, eou=0.30)
    await _run_turn(session, eou=0.30)
    session.emit("close", _UserState("listening", "listening"))
    await _drain()

    rows = await _stored(sink, sid)
    assert [r.turn_index for r in rows] == [0, 1]
    for row in rows:
        assert row.response_speed_ms is not None, (
            f"turn {row.turn_index} was not corrected; the precise latch did "
            "not reset between turns"
        )
        assert row.response_speed_ms >= 300


# --- the tracker's own contract ------------------------------------------


async def test_a_precise_report_overwrites_an_imprecise_one() -> None:
    """The ordering trap, stated directly on the tracker.

    This is the failure mode where everything looks fine: the imprecise value
    always arrives first, so a plain first-wins guard discards the correction,
    the tests still pass, and not one number moves.
    """
    captured: list[list[TurnRow]] = []

    async def flush(rows: list[TurnRow]) -> None:
        captured.append(rows)

    tracker = TurnTracker(flush_callback=flush, flush_size=100)
    await tracker.on_user_started_speaking(session_id="s", at_ms=1000)
    await tracker.on_user_stopped_speaking(session_id="s", at_ms=2000)  # biased
    await tracker.on_user_stopped_speaking(session_id="s", at_ms=1450, precise=True)
    await tracker.on_agent_audio_first_frame(session_id="s", at_ms=2500)
    await tracker.close_session("s")

    [turn] = captured[0]
    assert turn.caller_speak_end_ms == 1450, "the correction was discarded"
    assert turn.response_speed_ms == 1050


async def test_a_second_precise_report_does_not_overwrite() -> None:
    """Precise is a one-way upgrade, not a free-for-all."""
    captured: list[list[TurnRow]] = []

    async def flush(rows: list[TurnRow]) -> None:
        captured.append(rows)

    tracker = TurnTracker(flush_callback=flush, flush_size=100)
    await tracker.on_user_started_speaking(session_id="s", at_ms=1000)
    await tracker.on_user_stopped_speaking(session_id="s", at_ms=1450, precise=True)
    await tracker.on_user_stopped_speaking(session_id="s", at_ms=1600, precise=True)
    await tracker.on_agent_audio_first_frame(session_id="s", at_ms=2500)
    await tracker.close_session("s")

    [turn] = captured[0]
    assert turn.caller_speak_end_ms == 1450


async def test_an_imprecise_report_never_overwrites_a_precise_one() -> None:
    """Order-independence: the correction can legitimately arrive first."""
    captured: list[list[TurnRow]] = []

    async def flush(rows: list[TurnRow]) -> None:
        captured.append(rows)

    tracker = TurnTracker(flush_callback=flush, flush_size=100)
    await tracker.on_user_started_speaking(session_id="s", at_ms=1000)
    await tracker.on_user_stopped_speaking(session_id="s", at_ms=1450, precise=True)
    await tracker.on_user_stopped_speaking(session_id="s", at_ms=2000)
    await tracker.on_agent_audio_first_frame(session_id="s", at_ms=2500)
    await tracker.close_session("s")

    [turn] = captured[0]
    assert turn.caller_speak_end_ms == 1450


async def test_without_the_gate_an_uncorrected_turn_still_reports() -> None:
    """``precise_end_required`` is opt-in, so other transports are unaffected.

    A framework whose stop event is the real stop should keep getting a number.
    Only the LiveKit path sets the gate.
    """
    captured: list[list[TurnRow]] = []

    async def flush(rows: list[TurnRow]) -> None:
        captured.append(rows)

    tracker = TurnTracker(flush_callback=flush, flush_size=100)
    await tracker.on_user_started_speaking(session_id="s", at_ms=1000)
    await tracker.on_user_stopped_speaking(session_id="s", at_ms=1500)
    await tracker.on_agent_audio_first_frame(session_id="s", at_ms=2000)
    await tracker.close_session("s")

    [turn] = captured[0]
    assert turn.response_speed_ms == 500
