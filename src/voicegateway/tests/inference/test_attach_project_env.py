"""Tests for VOICEGW_PROJECT env-var fallback on voicegateway.attach().

Precedence (high to low):
  1. explicit ``project=`` argument
  2. ``VOICEGW_PROJECT`` environment variable
  3. ``"default"``
"""

from __future__ import annotations

from typing import Any


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


# ---------------------------------------------------------------------------
# 1. Explicit project= wins even when VOICEGW_PROJECT is set
# ---------------------------------------------------------------------------


async def test_attach_explicit_project_wins_over_env(monkeypatch):
    """attach(project='explicit') uses 'explicit' even when VOICEGW_PROJECT is set."""
    import voicegateway

    monkeypatch.setenv("VOICEGW_PROJECT", "from-env")

    sink = _CaptureSink()
    llm = _FakeEmitter()
    session = _FakeSession(llm=llm)

    voicegateway.attach(session, project="explicit", sink=sink)
    llm.emit("metrics_collected", _LLMMetric())
    await session._vg_capture.drain()

    assert len(sink.rows) == 1
    assert sink.rows[0].project == "explicit"


# ---------------------------------------------------------------------------
# 2. VOICEGW_PROJECT is used when no explicit project= is given
# ---------------------------------------------------------------------------


async def test_attach_uses_voicegw_project_env_when_no_arg(monkeypatch):
    """attach() without project= picks up VOICEGW_PROJECT from the environment."""
    import voicegateway

    monkeypatch.setenv("VOICEGW_PROJECT", "from-env")

    sink = _CaptureSink()
    llm = _FakeEmitter()
    session = _FakeSession(llm=llm)

    voicegateway.attach(session, sink=sink)
    llm.emit("metrics_collected", _LLMMetric())
    await session._vg_capture.drain()

    assert len(sink.rows) == 1
    assert sink.rows[0].project == "from-env"


# ---------------------------------------------------------------------------
# 3. Falls back to "default" when neither arg nor env is set
# ---------------------------------------------------------------------------


async def test_attach_falls_back_to_default_when_neither_set(monkeypatch):
    """attach() with no project= and no VOICEGW_PROJECT uses 'default'."""
    import voicegateway

    monkeypatch.delenv("VOICEGW_PROJECT", raising=False)

    sink = _CaptureSink()
    llm = _FakeEmitter()
    session = _FakeSession(llm=llm)

    voicegateway.attach(session, sink=sink)
    llm.emit("metrics_collected", _LLMMetric())
    await session._vg_capture.drain()

    assert len(sink.rows) == 1
    assert sink.rows[0].project == "default"
