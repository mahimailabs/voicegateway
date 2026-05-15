"""Contract tests for voicegateway.middleware.turn_tracker_middleware."""

from __future__ import annotations

import pytest

from voicegateway.middleware.turn_tracker_middleware import TurnRow, TurnTracker


async def test_normal_turn_lifecycle_produces_one_turn_row() -> None:
    captured: list[list[TurnRow]] = []

    async def flush(rows: list[TurnRow]) -> None:
        captured.append(rows)

    tracker = TurnTracker(flush_callback=flush, flush_size=10)

    await tracker.on_user_started_speaking(session_id="s1", at_ms=1000)
    await tracker.on_user_stopped_speaking(session_id="s1", at_ms=2000)
    await tracker.on_agent_audio_first_frame(session_id="s1", at_ms=2200)
    await tracker.on_agent_audio_last_frame(session_id="s1", at_ms=4000)

    flushed = await tracker.close_session("s1")
    assert flushed == 1
    assert len(captured) == 1
    [turn] = captured[0]
    assert turn.session_id == "s1"
    assert turn.turn_index == 0
    assert turn.caller_speak_start_ms == 1000
    assert turn.caller_speak_end_ms == 2000
    assert turn.agent_speak_start_ms == 2200
    assert turn.agent_speak_end_ms == 4000
    assert turn.response_speed_ms == 200


async def test_missed_user_stopped_event_yields_null_response_speed() -> None:
    captured: list[list[TurnRow]] = []

    async def flush(rows: list[TurnRow]) -> None:
        captured.append(rows)

    tracker = TurnTracker(flush_callback=flush)

    await tracker.on_user_started_speaking(session_id="s1", at_ms=1000)
    # user_stopped is missed
    await tracker.on_agent_audio_first_frame(session_id="s1", at_ms=2200)

    await tracker.close_session("s1")
    [turn] = captured[0]
    assert turn.response_speed_ms is None
    assert turn.caller_speak_end_ms == 2200  # inferred from agent first frame


async def test_agent_never_speaks_yields_null_response_speed_on_close() -> None:
    captured: list[list[TurnRow]] = []

    async def flush(rows: list[TurnRow]) -> None:
        captured.append(rows)

    tracker = TurnTracker(flush_callback=flush)

    await tracker.on_user_started_speaking(session_id="s1", at_ms=1000)
    await tracker.on_user_stopped_speaking(session_id="s1", at_ms=2000)
    # No agent events; close emits the tail turn.

    await tracker.close_session("s1")
    [turn] = captured[0]
    assert turn.agent_speak_start_ms is None
    assert turn.agent_speak_end_ms is None
    assert turn.response_speed_ms is None
    assert turn.caller_speak_end_ms == 2000


async def test_auto_flush_at_buffer_size() -> None:
    captured: list[list[TurnRow]] = []

    async def flush(rows: list[TurnRow]) -> None:
        captured.append(list(rows))

    tracker = TurnTracker(flush_callback=flush, flush_size=2)

    for i in range(2):
        await tracker.on_user_started_speaking(session_id="s1", at_ms=1000 + i * 1000)
        await tracker.on_user_stopped_speaking(session_id="s1", at_ms=1500 + i * 1000)
        await tracker.on_agent_audio_first_frame(session_id="s1", at_ms=1600 + i * 1000)

    # After the second turn closes, buffer hits flush_size=2 and auto-flushes.
    assert len(captured) == 1
    assert len(captured[0]) == 2


async def test_multi_session_isolation() -> None:
    captured: dict[str, list[TurnRow]] = {"a": [], "b": []}

    async def flush(rows: list[TurnRow]) -> None:
        for r in rows:
            captured[r.session_id].append(r)

    tracker = TurnTracker(flush_callback=flush, flush_size=100)

    await tracker.on_user_started_speaking(session_id="a", at_ms=1000)
    await tracker.on_user_started_speaking(session_id="b", at_ms=1100)
    await tracker.on_user_stopped_speaking(session_id="a", at_ms=2000)
    await tracker.on_user_stopped_speaking(session_id="b", at_ms=2100)
    await tracker.on_agent_audio_first_frame(session_id="a", at_ms=2200)
    await tracker.on_agent_audio_first_frame(session_id="b", at_ms=2300)

    await tracker.close_session("a")
    await tracker.close_session("b")

    assert len(captured["a"]) == 1
    assert len(captured["b"]) == 1
    assert captured["a"][0].response_speed_ms == 200
    assert captured["b"][0].response_speed_ms == 200


async def test_flush_size_validation() -> None:
    with pytest.raises(ValueError):
        TurnTracker(flush_size=0)
    with pytest.raises(ValueError):
        TurnTracker(flush_size=-1)


async def test_duplicate_user_started_is_idempotent() -> None:
    captured: list[list[TurnRow]] = []

    async def flush(rows: list[TurnRow]) -> None:
        captured.append(rows)

    tracker = TurnTracker(flush_callback=flush)

    await tracker.on_user_started_speaking(session_id="s1", at_ms=1000)
    await tracker.on_user_started_speaking(session_id="s1", at_ms=1500)  # ignored
    await tracker.on_user_stopped_speaking(session_id="s1", at_ms=2000)
    await tracker.on_agent_audio_first_frame(session_id="s1", at_ms=2200)

    await tracker.close_session("s1")
    [turn] = captured[0]
    # First user_started_speaking wins.
    assert turn.caller_speak_start_ms == 1000


async def test_close_unknown_session_is_noop() -> None:
    tracker = TurnTracker()
    flushed = await tracker.close_session("never-existed")
    assert flushed == 0


async def test_active_sessions_lists_in_flight() -> None:
    tracker = TurnTracker()
    assert tracker.active_sessions() == []

    await tracker.on_user_started_speaking(session_id="s1")
    assert tracker.active_sessions() == ["s1"]

    await tracker.close_session("s1")
    assert tracker.active_sessions() == []
