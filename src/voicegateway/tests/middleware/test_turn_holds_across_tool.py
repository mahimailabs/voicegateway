"""A turn covering a tool call must close on the ANSWER, not on the filler.

`response_speed_ms` is `agent_speak_start_ms - caller_speak_end_ms`, and the
turn closes on the agent's first audio frame. When an agent covers a slow tool
call with a holding line ("let me pull that up"), that holding line IS the first
frame, so the row reported the time to the filler and not to the answer.

Filler during tool calls is a standard pattern rather than an exotic one:
`livekit-agents` ships `RunContext.with_filler` for exactly it. The error is
also the worst possible shape for an aggregate. Filler turns report FAST
numbers, so they pull p50 down instead of standing out as outliers, and the one
turn somebody opened the latency view to investigate is the one that lies.

The fix holds the turn open while any tool call is in flight. These tests cover
the four cases the issue names, and the third and fourth are the ones that
matter: a turn with no tool must be untouched, and a cancelled tool must not
leave the turn open to swallow the caller's next utterance.
"""

from __future__ import annotations

from voicegateway.middleware.turn_tracker_middleware import TurnTracker

CALL = "call_1"


def _tracker() -> TurnTracker:
    return TurnTracker(flush_size=1000)


def _only(tracker: TurnTracker, sid: str = "s"):
    state = tracker._sessions[sid]
    assert len(state.buffered_turns) == 1, (
        f"expected exactly one turn, got {len(state.buffered_turns)}"
    )
    return state.buffered_turns[0]


async def test_a_turn_with_no_tool_is_unchanged() -> None:
    """The regression guard. Most turns take this path and must not move."""
    t = _tracker()
    await t.on_user_started_speaking(session_id="s", at_ms=1000)
    await t.on_user_stopped_speaking(session_id="s", at_ms=2000, precise=True)
    await t.on_agent_audio_first_frame(session_id="s", at_ms=2300)
    turn = _only(t)
    assert turn.agent_speak_start_ms == 2300
    assert turn.response_speed_ms == 300


async def test_filler_then_answer_reports_the_answer() -> None:
    """The defect. Filler at 2300, tool ends at 5000, answer at 5200.

    Closing on the filler reports 300ms for a wait the caller measured at
    3200ms: an order of magnitude, in the flattering direction.
    """
    t = _tracker()
    await t.on_user_started_speaking(session_id="s", at_ms=1000)
    await t.on_user_stopped_speaking(session_id="s", at_ms=2000, precise=True)
    await t.on_tool_started(session_id="s", call_id=CALL)
    await t.on_agent_audio_first_frame(session_id="s", at_ms=2300)  # filler
    assert not t._sessions["s"].buffered_turns, "closed on the holding line"
    await t.on_tool_ended(session_id="s", call_id=CALL)
    await t.on_agent_audio_first_frame(session_id="s", at_ms=5200)  # answer
    turn = _only(t)
    assert turn.agent_speak_start_ms == 5200
    assert turn.response_speed_ms == 3200


async def test_a_tool_faster_than_the_filler_delay_plays_no_filler() -> None:
    """The tool returns before any holding line is spoken.

    There is then ONE audio frame and it is the answer, so the turn closes on it
    exactly as it would with no tool at all. Nothing may be left open waiting for
    a second frame that is never coming.
    """
    t = _tracker()
    await t.on_user_started_speaking(session_id="s", at_ms=1000)
    await t.on_user_stopped_speaking(session_id="s", at_ms=2000, precise=True)
    await t.on_tool_started(session_id="s", call_id=CALL)
    await t.on_tool_ended(session_id="s", call_id=CALL)
    await t.on_agent_audio_first_frame(session_id="s", at_ms=2400)
    turn = _only(t)
    assert turn.agent_speak_start_ms == 2400
    assert turn.response_speed_ms == 400


async def test_a_cancelled_tool_does_not_swallow_the_next_turn() -> None:
    """The worst failure this change could introduce, asserted directly.

    A cancelled tool that stayed in flight would leave the turn open, so the
    caller's NEXT utterance would be absorbed into it and every later row in the
    call would be wrong. That is worse than the mistimed row being fixed, so
    cancelled clears the tool exactly as a completed one does.
    """
    t = _tracker()
    await t.on_user_started_speaking(session_id="s", at_ms=1000)
    await t.on_user_stopped_speaking(session_id="s", at_ms=2000, precise=True)
    await t.on_tool_started(session_id="s", call_id=CALL)
    await t.on_tool_ended(session_id="s", call_id=CALL)  # cancelled: same event
    await t.on_agent_audio_first_frame(session_id="s", at_ms=2500)
    await t.on_agent_audio_last_frame(session_id="s", at_ms=3000)

    await t.on_user_started_speaking(session_id="s", at_ms=4000)
    await t.on_user_stopped_speaking(session_id="s", at_ms=4500, precise=True)
    await t.on_agent_audio_first_frame(session_id="s", at_ms=4800)

    turns = t._sessions["s"].buffered_turns
    assert len(turns) == 2, f"the second turn was swallowed: {turns}"
    assert turns[1].caller_speak_start_ms == 4000
    assert turns[1].response_speed_ms == 300


async def test_two_tools_in_flight_need_both_to_end() -> None:
    """Tracked by call_id, so one of two finishing does not release the turn."""
    t = _tracker()
    await t.on_user_started_speaking(session_id="s", at_ms=1000)
    await t.on_user_stopped_speaking(session_id="s", at_ms=2000, precise=True)
    await t.on_tool_started(session_id="s", call_id="a")
    await t.on_tool_started(session_id="s", call_id="b")
    await t.on_tool_ended(session_id="s", call_id="a")
    await t.on_agent_audio_first_frame(session_id="s", at_ms=3000)
    assert not t._sessions["s"].buffered_turns, "released while 'b' still ran"
    await t.on_tool_ended(session_id="s", call_id="b")
    await t.on_agent_audio_first_frame(session_id="s", at_ms=6000)
    assert _only(t).response_speed_ms == 4000
