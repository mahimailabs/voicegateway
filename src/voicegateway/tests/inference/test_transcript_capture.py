"""attach transcript capture: history extraction + kill-switch."""

from __future__ import annotations

import pytest

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


@pytest.mark.parametrize(
    "env,param,expected",
    [
        (None, True, True),
        (None, False, False),
        ("0", True, False),
        ("false", True, False),
        ("off", True, False),
        ("OFF", True, False),
        ("no", True, False),
        ("1", True, True),
    ],
)
def test_transcripts_kill_switch(monkeypatch, env, param, expected):
    """VOICEGW_TRANSCRIPTS falsy disables capture; unset or truthy keeps the param."""
    if env is None:
        monkeypatch.delenv("VOICEGW_TRANSCRIPTS", raising=False)
    else:
        monkeypatch.setenv("VOICEGW_TRANSCRIPTS", env)
    assert _transcripts_enabled(param) is expected
