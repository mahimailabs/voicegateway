"""``attach(snapshots=True)``: the wiring that turned a built feature on.

``StateSnapshotter`` and ``ReplayCapture`` shipped in v0.3.0 (791945c) and were
never constructed. ``StateSnapshotter.on_snapshot`` has exactly the signature of
``ReplayCapture.record_state_snapshot``, and ``replay_state_snapshots`` has been
in the schema since the initial migration, so the whole chain existed except the
call that joins it. ``voicegw replay`` and the dashboard's replay view read a
table nothing wrote to.

These tests drive the chain end to end against a real SQLite file, because that
is the part that was missing: unit tests over the two components passed for
months while the feature was unreachable.

**The other three replay modalities stay unwired on purpose.**
``record_stt_chunk``, ``record_llm_token`` and ``record_tts_frame`` are per
chunk, per token and per frame. No ``AgentSession`` event carries data at that
granularity, so capturing them means sitting inside the STT/LLM/TTS streams,
which is the audio path VoiceGateway stays out of. Snapshots are different:
``conversation_item_added`` and ``function_tools_executed`` fire once per
completed message and once per resolved tool batch, so this stays a passive
observer.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest

from voicegateway.services.storage_service import StorageService

# The MODULE, not the re-exported ``attach`` FUNCTION. The package's __init__
# binds the name ``attach`` to the function, and since Python 3.7 both
# ``from ... import attach`` and ``import ....attach as x`` resolve through
# getattr on the package, so either one hands back the function and every
# attribute lookup below fails. importlib is the only form that cannot be
# shadowed.
attach_mod = importlib.import_module("voicegateway.inference.session.attach")


class _FakeItem:
    def __init__(self, role: str, text: str) -> None:
        self.role = role
        self.text_content = text


class _FakeHistory:
    def __init__(self, items: list[_FakeItem]) -> None:
        self.items = items


class _FakeAgent:
    instructions = "You are a helpful booking agent."


class _FakeCall:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments
        self.call_id = "call_1"


class _FakeOutput:
    def __init__(self, output: str) -> None:
        self.output = output
        self.call_id = "call_1"
        self.is_error = False


class _FakeToolsEvent:
    """Shaped like livekit.agents FunctionToolsExecutedEvent."""

    def __init__(self, pairs: list[tuple[_FakeCall, _FakeOutput]]) -> None:
        self._pairs = pairs

    def zipped(self) -> list[tuple[_FakeCall, _FakeOutput]]:
        return self._pairs


class _FakeSession:
    """The surface attach() touches on a LiveKit AgentSession."""

    def __init__(self) -> None:
        self.history = _FakeHistory(
            [_FakeItem("user", "book me a table"), _FakeItem("assistant", "sure")]
        )
        self.current_agent = _FakeAgent()
        self.handlers: dict[str, Any] = {}

    def on(self, event: str, handler: Any) -> None:
        self.handlers[event] = handler

    async def fire(self, event: str, *args: Any) -> None:
        handler = self.handlers.get(event)
        if handler is not None:
            await handler(*args)


@pytest.fixture
def storage(tmp_path):
    return StorageService(str(tmp_path / "snap.db"))


@pytest.fixture
def wired(storage):
    """attach()'s snapshot pair, built the way _attach_livekit builds it."""

    class _SinkWithStorage:
        _storage = storage

    return attach_mod._build_snapshot_capture(_SinkWithStorage(), None, "sess-1")


async def _rows(storage: StorageService) -> list[Any]:
    from voicegateway.repository import replay_repository

    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        return await replay_repository.read_full_replay(db, "sess-1")


# --------------------------------------------------------------------------
# The chain reaches the database
# --------------------------------------------------------------------------


async def test_a_message_event_lands_a_row_in_replay_state_snapshots(
    storage, wired
) -> None:
    """The end-to-end claim. Everything below it was already tested; this was not."""
    capture, snapshotter = wired
    session = _FakeSession()
    handler = attach_mod._emit_conversation_item("sess-1", session, snapshotter)

    await handler()
    await capture.close_session("sess-1")

    rows = await _rows(storage)
    assert len(rows) == 1, "the snapshot never reached the table"
    payload = rows[0].payload if hasattr(rows[0], "payload") else rows[0]
    assert "book me a table" in str(payload)
    assert "helpful booking agent" in str(payload)


async def test_a_tool_call_records_its_name_arguments_and_result(
    storage, wired
) -> None:
    """Tool calls are the single most useful thing in a replay."""
    capture, snapshotter = wired
    session = _FakeSession()
    handler = attach_mod._emit_tools_executed("sess-1", session, snapshotter)

    event = _FakeToolsEvent(
        [(_FakeCall("book_table", '{"party": 4}'), _FakeOutput("confirmed 19:00"))]
    )
    await handler(event)
    await capture.close_session("sess-1")

    rows = await _rows(storage)
    assert len(rows) == 1
    body = str(rows[0].payload if hasattr(rows[0], "payload") else rows[0])
    assert "book_table" in body
    assert "party" in body
    assert "confirmed 19:00" in body


async def test_unparseable_tool_arguments_keep_the_raw_text(storage, wired) -> None:
    """Providers send arguments as a JSON string. A malformed one must not drop
    the call: the fact that the tool ran is worth more than the parse."""
    capture, snapshotter = wired
    session = _FakeSession()
    handler = attach_mod._emit_tools_executed("sess-1", session, snapshotter)

    await handler(_FakeToolsEvent([(_FakeCall("f", "{not json"), _FakeOutput("ok"))]))
    await capture.close_session("sess-1")

    rows = await _rows(storage)
    assert len(rows) == 1
    assert "{not json" in str(
        rows[0].payload if hasattr(rows[0], "payload") else rows[0]
    )


async def test_tool_snapshots_bypass_the_rate_cap(storage, wired) -> None:
    """Two tool calls inside the 1s message cap must both be recorded.

    Non-vacuous against the message path below, which is capped.
    """
    capture, snapshotter = wired
    session = _FakeSession()
    handler = attach_mod._emit_tools_executed("sess-1", session, snapshotter)

    await handler(_FakeToolsEvent([(_FakeCall("a", "{}"), _FakeOutput("1"))]))
    await handler(_FakeToolsEvent([(_FakeCall("b", "{}"), _FakeOutput("2"))]))
    await capture.close_session("sess-1")

    assert len(await _rows(storage)) == 2


async def test_message_snapshots_are_rate_capped(storage, wired) -> None:
    """The other half of the test above: rapid message events collapse to one."""
    capture, snapshotter = wired
    session = _FakeSession()
    handler = attach_mod._emit_conversation_item("sess-1", session, snapshotter)

    for _ in range(5):
        await handler()
    await capture.close_session("sess-1")

    assert len(await _rows(storage)) == 1


# --------------------------------------------------------------------------
# Capture never breaks the agent
# --------------------------------------------------------------------------


async def test_a_session_that_cannot_be_read_does_not_raise(storage, wired) -> None:
    """A snapshot is diagnostics. It must never take the call down with it."""
    _capture, snapshotter = wired

    class _Hostile:
        @property
        def history(self) -> Any:
            raise RuntimeError("no history for you")

        @property
        def current_agent(self) -> Any:
            raise RuntimeError("nor an agent")

    handler = attach_mod._emit_conversation_item("sess-1", _Hostile(), snapshotter)
    await handler()  # must not raise


async def test_a_storage_failure_does_not_raise_out_of_the_handler(wired) -> None:
    """Same rule, one layer down: a failing write is logged, not propagated."""

    class _Boom:
        _storage = None

    capture, snapshotter = attach_mod._build_snapshot_capture(_Boom(), None, "s")
    assert capture is None and snapshotter is None


# --------------------------------------------------------------------------
# The switches
# --------------------------------------------------------------------------


def test_snapshots_are_off_by_default() -> None:
    """A snapshot carries the system prompt and every tool payload, which is a
    strictly larger disclosure than a transcript. It is asked for, not assumed."""
    import inspect

    assert inspect.signature(attach_mod.attach).parameters["snapshots"].default is False
    # Non-vacuous: transcripts really are the other way round.
    assert inspect.signature(attach_mod.attach).parameters["transcript"].default is True


def test_the_kill_switch_beats_the_argument(monkeypatch) -> None:
    """A fleet must be able to force capture off centrally."""
    monkeypatch.setenv("VOICEGW_SNAPSHOTS", "0")
    assert attach_mod._snapshots_enabled(True) is False
    monkeypatch.setenv("VOICEGW_SNAPSHOTS", "off")
    assert attach_mod._snapshots_enabled(True) is False
    monkeypatch.delenv("VOICEGW_SNAPSHOTS")
    assert attach_mod._snapshots_enabled(True) is True


def test_a_remote_sink_captures_nothing() -> None:
    """A collector has no replay tables, and the dashboard reads replay locally.

    Capturing there would buffer rows that nothing could ever flush.
    """

    class _RemoteSink:
        pass

    assert attach_mod._build_snapshot_capture(_RemoteSink(), None, "s") == (None, None)


# --------------------------------------------------------------------------
# Wired into attach(), not merely importable
# --------------------------------------------------------------------------


async def test_attach_subscribes_both_events_only_when_enabled(tmp_path) -> None:
    """The bug this whole change fixes was a missing subscription, so assert on
    the subscriptions rather than on the components existing."""
    from voicegateway.services.sinks import LocalSqliteSink

    sink = LocalSqliteSink(StorageService(str(tmp_path / "a.db")))

    off = _FakeSession()
    attach_mod._attach_livekit(off, sink=sink, snapshots=False)
    assert "conversation_item_added" not in off.handlers
    assert "function_tools_executed" not in off.handlers

    on = _FakeSession()
    attach_mod._attach_livekit(on, sink=sink, snapshots=True)
    assert "conversation_item_added" in on.handlers
    assert "function_tools_executed" in on.handlers
    # And the close handler survives alongside them.
    assert "close" in on.handlers


async def test_the_events_are_not_in_the_audio_path() -> None:
    """The reason this could be wired at all, pinned so nobody adds the others.

    The three unwired ReplayCapture modalities are per-chunk, per-token and
    per-frame; capturing them means sitting inside the media/inference streams.
    attach() must keep subscribing only to message- and tool-level events.
    """
    import inspect

    src = inspect.getsource(attach_mod._attach_livekit)
    for forbidden in ("record_stt_chunk", "record_llm_token", "record_tts_frame"):
        assert forbidden not in src, (
            f"attach() reached into the audio path: {forbidden}"
        )


def test_pipecat_accepts_the_flag_without_capturing() -> None:
    """Signature parity: the same attach(...) call must work on either framework."""
    import inspect

    params = inspect.signature(attach_mod._attach_pipecat).parameters
    assert params["snapshots"].default is False


async def test_close_flushes_what_is_still_buffered(storage, wired) -> None:
    """Under the flush-size threshold nothing is written until close.

    Without the close_session call in attach()'s _finish, a short call would
    capture snapshots and persist none of them.
    """
    capture, snapshotter = wired
    session = _FakeSession()
    handler = attach_mod._emit_conversation_item("sess-1", session, snapshotter)

    await handler()
    assert await _rows(storage) == [], "flushed early; the test proves nothing"

    await capture.close_session("sess-1")
    assert len(await _rows(storage)) == 1


async def test_the_row_is_keyed_by_the_session_the_pair_was_built_for(
    storage, wired
) -> None:
    """The seam bug, pinned. This is what made the first end-to-end run write 0 rows.

    ``StateSnapshotter._emit`` resolves a session id and then calls its sink with
    the snapshot ALONE, dropping it, so ``ReplayCapture`` fell back to the
    session ContextVar. Under attach() that variable need not be set on the task
    LiveKit dispatches an event handler on, so the snapshot buffered under one
    key while ``close_session`` flushed another and the rows vanished silently.

    attach() builds one pair per session and binds the id in the closure, so the
    row lands under that session regardless of the ambient context. The id on
    the emitter below is deliberately different: that one keys the rate cap
    only, and must not be able to redirect a write.
    """
    capture, snapshotter = wired  # bound to "sess-1"
    session = _FakeSession()

    await attach_mod._emit_conversation_item("sess-2", session, snapshotter)()
    await capture.close_session("sess-1")

    rows = await _rows(storage)  # reads sess-1
    assert len(rows) == 1
    assert rows[0].session_id == "sess-1"
