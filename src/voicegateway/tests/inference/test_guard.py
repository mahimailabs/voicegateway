"""Tests for voicegateway.guard(): control-only wrapper (LiveKit).

guard() is the ACTIVE control seam (fallback / rate-limit / budget). It composes
with attach() (the sole meter) and writes NO metrics itself. These tests use
fake LiveKit plugins (real ``livekit.agents.{stt,llm,tts}`` subclasses so the
isinstance dispatch + component_identity work) driven directly, plus a fake
session bound to a MetricCapture to prove no double-count and the fallback_from
stamp.
"""

from __future__ import annotations

from typing import Any

import pytest
from livekit.agents import llm as lk_llm
from livekit.agents import stt as lk_stt

import voicegateway
from voicegateway.guard import (
    RateLimitSpec,
    parse_budget,
    parse_rate_limit,
)
from voicegateway.inference.session.capture import MetricCapture
from voicegateway.inference.session.context import (
    reset_guard_fallback_from,
)
from voicegateway.middleware.budget_enforcer_middleware import BudgetExceededError
from voicegateway.middleware.cost_tracker_middleware import CostTracker
from voicegateway.middleware.rate_limiter_middleware import RateLimitExceeded

# --- fake LiveKit plugins (real subclasses so isinstance dispatch works) ----


class _FakeLLM(lk_llm.LLM):
    def __init__(self, *, model: str, provider: str, fail: bool = False) -> None:
        super().__init__()
        self._model = model
        self._provider_id = provider
        self._fail = fail
        self.chat_calls = 0

    @property
    def model(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return self._provider_id

    def chat(self, **kwargs: Any) -> Any:
        self.chat_calls += 1
        return _FakeLLMStream(fail=self._fail)


class _FakeLLMStream:
    def __init__(self, *, fail: bool) -> None:
        self._fail = fail

    def __aiter__(self) -> Any:
        return self._agen()

    async def _agen(self) -> Any:
        if self._fail:
            raise RuntimeError("primary llm boom")
        for token in ("hello", "world"):
            yield token

    async def aclose(self) -> None:
        pass


class _FakeSTT(lk_stt.STT):
    def __init__(self, *, model: str, provider: str, fail: bool = False) -> None:
        super().__init__(
            capabilities=lk_stt.STTCapabilities(streaming=False, interim_results=False)
        )
        self._model = model
        self._provider_id = provider
        self._fail = fail
        self.calls = 0

    @property
    def model(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return self._provider_id

    async def _recognize_impl(self, buffer: Any, **kwargs: Any) -> Any:
        self.calls += 1
        if self._fail:
            raise RuntimeError("primary stt boom")
        return f"transcript from {self._provider_id}"

    async def recognize(self, buffer: Any, **kwargs: Any) -> Any:
        self.calls += 1
        if self._fail:
            raise RuntimeError("primary stt boom")
        return f"transcript from {self._provider_id}"


# --- an event-emitter double used to bind attach's MetricCapture ------------


class _Emitter:
    """Duck-typed LiveKit plugin: .on()/.emit() with model/provider."""

    def __init__(self, *, model: str, provider: str) -> None:
        self.model = model
        self.provider = provider
        self._handlers: dict[str, list[Any]] = {}

    def on(self, event: str, handler: Any) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def emit(self, event: str, *args: Any) -> None:
        for handler in list(self._handlers.get(event, [])):
            handler(*args)


class _Session:
    def __init__(self, *, stt: Any = None, llm: Any = None, tts: Any = None) -> None:
        self.stt = stt
        self.llm = llm
        self.tts = tts
        self._handlers: dict[str, list[Any]] = {}

    def on(self, event: str, handler: Any) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def emit(self, event: str, *args: Any) -> None:
        for handler in list(self._handlers.get(event, [])):
            handler(*args)


class _RecordingSink:
    def __init__(self) -> None:
        self.rows: list = []

    async def log_request(self, record: Any) -> None:
        self.rows.append(record)

    async def flush(self) -> None:
        pass

    async def aclose(self) -> None:
        pass


class _LLMMetric:
    prompt_tokens = 100
    completion_tokens = 50
    prompt_cached_tokens = 0
    ttft = 0.2


@pytest.fixture(autouse=True)
def _reset_fallback_marker():
    reset_guard_fallback_from()
    yield
    reset_guard_fallback_from()


# --- DSL parsing ------------------------------------------------------------


def test_parse_rate_limit_per_minute():
    spec = parse_rate_limit("60/min")
    assert spec == RateLimitSpec(requests=60, per_seconds=60.0)
    assert spec.requests_per_minute == 60


def test_parse_rate_limit_per_second():
    spec = parse_rate_limit("5/s")
    assert spec.requests == 5
    assert spec.per_seconds == 1.0
    assert spec.requests_per_minute == 300


def test_parse_rate_limit_rejects_garbage():
    with pytest.raises(ValueError):
        parse_rate_limit("banana")


def test_parse_budget_dollar_per_day():
    spec = parse_budget("$5.00/day")
    assert spec.amount_usd == 5.0
    assert spec.window == "day"
    assert spec.period == "today"


def test_parse_budget_per_month_no_dollar():
    spec = parse_budget("100/month")
    assert spec.amount_usd == 100.0
    assert spec.window == "month"
    assert spec.period == "month"


def test_parse_budget_rejects_garbage():
    with pytest.raises(ValueError):
        parse_budget("5 dollars a fortnight")


# --- guard returns a drop-in of the same framework type ---------------------


def test_guard_llm_returns_lk_llm_subclass():
    primary = _FakeLLM(model="gpt-4o-mini", provider="openai")
    guarded = voicegateway.guard(primary)
    assert isinstance(guarded, lk_llm.LLM)


def test_guard_stt_returns_lk_stt_subclass():
    primary = _FakeSTT(model="nova-3", provider="deepgram")
    guarded = voicegateway.guard(primary)
    assert isinstance(guarded, lk_stt.STT)


def test_guard_rejects_unknown_provider():
    with pytest.raises((ValueError, TypeError)):
        voicegateway.guard(object())


# --- rate limit -------------------------------------------------------------


async def test_guard_rate_limit_throttles_llm():
    """A 1/min bucket admits the first call and rejects the second."""
    primary = _FakeLLM(model="gpt-4o-mini", provider="openai")
    guarded = voicegateway.guard(primary, rate_limit="1/min")

    # First call: consumes the single token and streams fine.
    stream1 = guarded.chat(chat_ctx=None)
    out = [tok async for tok in stream1]
    assert out == ["hello", "world"]

    # Second call within the same minute: bucket empty -> raises.
    stream2 = guarded.chat(chat_ctx=None)
    with pytest.raises(RateLimitExceeded):
        _ = [tok async for tok in stream2]


async def test_guard_rate_limit_throttles_stt():
    primary = _FakeSTT(model="nova-3", provider="deepgram")
    guarded = voicegateway.guard(primary, rate_limit="1/min")

    assert await guarded.recognize(b"audio") == "transcript from deepgram"
    with pytest.raises(RateLimitExceeded):
        await guarded.recognize(b"audio")


# --- budget -----------------------------------------------------------------


async def test_guard_budget_blocks_when_over_window_spend():
    """When accumulated spend >= cap, the guarded call raises BudgetExceededError."""
    primary = _FakeLLM(model="gpt-4o-mini", provider="openai")

    async def _spend_reader(project: str, period: str) -> float:
        # Already spent $6 today; cap is $5/day.
        return 6.0

    from voicegateway.inference.livekit.guard_livekit import guard_livekit

    guarded = guard_livekit(primary, budget="$5.00/day", spend_reader=_spend_reader)
    stream = guarded.chat(chat_ctx=None)
    with pytest.raises(BudgetExceededError):
        _ = [tok async for tok in stream]


async def test_guard_budget_allows_when_under_window_spend():
    primary = _FakeLLM(model="gpt-4o-mini", provider="openai")

    async def _spend_reader(project: str, period: str) -> float:
        return 1.0  # under the $5 cap

    from voicegateway.inference.livekit.guard_livekit import guard_livekit

    guarded = guard_livekit(primary, budget="$5.00/day", spend_reader=_spend_reader)
    stream = guarded.chat(chat_ctx=None)
    out = [tok async for tok in stream]
    assert out == ["hello", "world"]


# --- fallback ---------------------------------------------------------------


async def test_guard_fallback_runs_secondary_on_primary_error():
    """When the primary stream errors, the fallback provider runs instead."""
    primary = _FakeLLM(model="gpt-4o-mini", provider="openai", fail=True)
    backup = _FakeLLM(model="claude-haiku", provider="anthropic", fail=False)
    guarded = voicegateway.guard(primary, fallback=[backup])

    stream = guarded.chat(chat_ctx=None)
    out = [tok async for tok in stream]
    assert out == ["hello", "world"]
    assert backup.chat_calls == 1


async def test_guard_fallback_stt_runs_secondary():
    primary = _FakeSTT(model="nova-3", provider="deepgram", fail=True)
    backup = _FakeSTT(model="whisper-1", provider="openai", fail=False)
    guarded = voicegateway.guard(primary, fallback=[backup])

    result = await guarded.recognize(b"audio")
    assert result == "transcript from openai"


async def test_guard_all_providers_fail_reraises():
    primary = _FakeSTT(model="nova-3", provider="deepgram", fail=True)
    backup = _FakeSTT(model="whisper-1", provider="openai", fail=True)
    guarded = voicegateway.guard(primary, fallback=[backup])
    with pytest.raises(RuntimeError):
        await guarded.recognize(b"audio")


async def test_guard_fallback_sets_contextvar_for_attach_stamp():
    """After a fallback, the guard ContextVar carries the primary provider so
    attach can stamp fallback_from on the record it writes."""
    from voicegateway.inference.session.context import current_guard_fallback_from

    primary = _FakeSTT(model="nova-3", provider="deepgram", fail=True)
    backup = _FakeSTT(model="whisper-1", provider="openai", fail=False)
    guarded = voicegateway.guard(primary, fallback=[backup])

    await guarded.recognize(b"audio")
    # The marker is the primary provider that was fallen back FROM.
    assert current_guard_fallback_from() == "deepgram"


async def test_attach_stamps_fallback_from_after_guard_fallback():
    """End-to-end: guard sets the marker; attach's MetricCapture stamps
    fallback_from + status='fallback' on the metered row."""
    from voicegateway.inference.session.context import set_guard_fallback_from

    sink = _RecordingSink()
    cost_tracker = CostTracker(sink)
    # The component attach binds to is an emitter for the provider that ran.
    ran = _Emitter(model="whisper-1", provider="openai")
    session = _Session(stt=ran)
    capture = MetricCapture(
        cost_tracker=cost_tracker,
        sink=sink,
        project="p",
        agent_id="a",
        session_id="s",
    )
    capture.bind(session)

    # Guard fell back from deepgram to openai for this call.
    set_guard_fallback_from("deepgram")
    ran.emit("metrics_collected", _STTMetric())
    await capture.drain()

    assert len(sink.rows) == 1
    assert sink.rows[0].fallback_from == "deepgram"
    assert sink.rows[0].status == "fallback"
    assert sink.rows[0].provider == "openai"


class _STTMetric:
    audio_duration = 60.0


# --- guard writes NO metrics itself -----------------------------------------


async def test_guard_writes_no_records():
    """Driving a guarded call writes zero rows through the guard itself; only
    attach (bound separately) would meter."""
    sink = _RecordingSink()
    primary = _FakeLLM(model="gpt-4o-mini", provider="openai")
    guarded = voicegateway.guard(primary)

    stream = guarded.chat(chat_ctx=None)
    _ = [tok async for tok in stream]

    # The guard shell's cost tracker has no storage and the wrapper is
    # metering=False, so nothing was written anywhere the guard controls.
    assert sink.rows == []
    # And the guard shell must not subscribe its own metering handler.
    assert object.__getattribute__(guarded, "_metering") is False


# --- no double count --------------------------------------------------------


async def test_no_double_count_guard_plus_attach():
    """A guard-wrapped component + attach(session): one metrics_collected event
    yields exactly ONE RequestRecord (attach meters; guard does not)."""
    sink = _RecordingSink()
    # The inner plugin is a real LK LLM (so guard accepts it) that can fire a
    # metrics_collected event. guard's wrapper forwards it transparently; attach
    # (bound to session.llm == the guard wrapper) meters it exactly once.
    inner = _FakeLLM(model="gpt-4o-mini", provider="openai")
    guarded = voicegateway.guard(inner)
    session = _Session(llm=guarded)

    voicegateway.attach(session, project="p", agent_id="a", sink=sink)

    inner.emit("metrics_collected", _LLMMetric())
    await session._vg_capture.drain()

    assert len(sink.rows) == 1, f"expected exactly one row, got {len(sink.rows)}"
    assert sink.rows[0].modality == "llm"
    assert sink.rows[0].provider == "openai"
