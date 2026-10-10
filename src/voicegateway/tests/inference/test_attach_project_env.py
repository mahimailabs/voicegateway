"""Tests for VOICEGW_PROJECT env-var fallback on voicegateway.attach().

Precedence (high to low):
  1. explicit ``project=`` argument
  2. ``VOICEGW_PROJECT`` environment variable
  3. ``"default"``
"""

from __future__ import annotations

from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Minimal fakes (mirroring the pattern in test_attach_capture.py)
# ---------------------------------------------------------------------------


class _FakeEmitter:
    def __init__(self, *, model: str = "gpt-4o-mini", provider: str = "openai") -> None:
        self.model = model
        self.provider = provider
        self._handlers: dict[str, list[Any]] = {}

    def on(self, event: str, handler: Any) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def emit(self, event: str, *args: Any) -> None:
        for handler in list(self._handlers.get(event, [])):
            handler(*args)


class _FakeSession:
    def __init__(self, *, llm: Any = None) -> None:
        self.llm = llm
        self.stt = None
        self.tts = None
        self._handlers: dict[str, list[Any]] = {}

    def on(self, event: str, handler: Any) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def emit(self, event: str, *args: Any) -> None:
        for handler in list(self._handlers.get(event, [])):
            handler(*args)


class _LLMMetric:
    prompt_tokens = 100
    completion_tokens = 50
    prompt_cached_tokens = 0
    ttft = 0.1


class _CaptureSink:
    """Minimal sink that records rows for inspection."""

    def __init__(self) -> None:
        self.rows: list = []

    async def log_request(self, record: Any) -> None:
        self.rows.append(record)

    async def flush(self) -> None:
        pass

    async def aclose(self) -> None:
        pass


@pytest.mark.parametrize(
    "env, kwargs, expected",
    [
        ("from-env", {"project": "explicit"}, "explicit"),  # explicit arg wins
        ("from-env", {}, "from-env"),  # env used when no arg
        (None, {}, "default"),  # neither set
    ],
)
async def test_attach_project_resolution(monkeypatch, env, kwargs, expected):
    import voicegateway

    if env is None:
        monkeypatch.delenv("VOICEGW_PROJECT", raising=False)
    else:
        monkeypatch.setenv("VOICEGW_PROJECT", env)

    sink = _CaptureSink()
    llm = _FakeEmitter()
    session = _FakeSession(llm=llm)

    voicegateway.attach(session, sink=sink, **kwargs)
    llm.emit("metrics_collected", _LLMMetric())
    await session._vg_capture.drain()

    assert len(sink.rows) == 1
    assert sink.rows[0].project == expected
