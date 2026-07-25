"""attach transcript capture: history extraction + kill-switch."""

from __future__ import annotations

from voicegateway.inference.session.attach import (
    _capture_transcript_from_history,
    _transcripts_enabled,
)


class _Msg:
    def __init__(self, role, text_content):
        self.role = role
        self.text_content = text_content


class _History:
    def __init__(self, items):
        self.items = items


class _Session:
    def __init__(self, items):
        self.history = _History(items)


class _FakeStorage:
    def __init__(self):
        self.calls = []

    async def write_transcript(self, session_id, turns, *, tenant_id=None):
        self.calls.append((session_id, turns, tenant_id))
        return len(turns)


async def test_capture_extracts_user_and_agent_text():
    session = _Session(
        [
            _Msg("user", "hello"),
            _Msg("assistant", "hi there"),
            _Msg("system", "you are a bot"),  # non-conversational role: dropped
            _Msg("user", "   "),  # blank: dropped
            _Msg("user", "bye"),
        ]
    )
    storage = _FakeStorage()
    await _capture_transcript_from_history(session, "s1", storage, "acme")
    assert storage.calls == [
        ("s1", [("user", "hello"), ("agent", "hi there"), ("user", "bye")], "acme")
    ]


async def test_capture_no_history_is_noop():
    storage = _FakeStorage()
    await _capture_transcript_from_history(_Session(None), "s1", storage, None)
    assert storage.calls == []


async def test_capture_never_raises_on_bad_storage():
    class Boom:
        async def write_transcript(self, *a, **k):
            raise RuntimeError("db down")

    # A capture failure must be swallowed, never surfaced to the agent.
    await _capture_transcript_from_history(
        _Session([_Msg("user", "hi")]), "s1", Boom(), None
    )


def test_transcripts_enabled_defaults_to_param(monkeypatch):
    monkeypatch.delenv("VOICEGW_TRANSCRIPTS", raising=False)
    assert _transcripts_enabled(True) is True
    assert _transcripts_enabled(False) is False


def test_transcripts_killswitch_disables(monkeypatch):
    for v in ("0", "false", "no", "off", "OFF"):
        monkeypatch.setenv("VOICEGW_TRANSCRIPTS", v)
        assert _transcripts_enabled(True) is False


def test_transcripts_killswitch_truthy_keeps_param(monkeypatch):
    monkeypatch.setenv("VOICEGW_TRANSCRIPTS", "1")
    assert _transcripts_enabled(True) is True
