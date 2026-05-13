"""Pin the LK-subclass contract that unblocks AC-2."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from livekit.agents import llm as lk_llm
from livekit.agents import stt as lk_stt
from livekit.agents import tts as lk_tts
from livekit.agents.stt import STTCapabilities
from livekit.agents.tts import TTSCapabilities

from voicegateway.middleware.instrumented_provider import (
    InstrumentedLLM,
    InstrumentedSTT,
    InstrumentedTTS,
)

# ---------------------------------------------------------------------------
# Lightweight LK-shaped fakes that subclass the real base classes so
# the wrapper's super().__init__ + event-bridge construction work.
# ---------------------------------------------------------------------------


class _RealSTT(lk_stt.STT):
    def __init__(self) -> None:
        super().__init__(
            capabilities=STTCapabilities(streaming=False, interim_results=False)
        )

    async def _recognize_impl(self, *args: Any, **kwargs: Any) -> Any:
        return None


class _RealLLM(lk_llm.LLM):
    def __init__(self) -> None:
        super().__init__()

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        return None


class _RealTTS(lk_tts.TTS):
    def __init__(self) -> None:
        super().__init__(
            capabilities=TTSCapabilities(streaming=False),
            sample_rate=24000,
            num_channels=1,
        )

    def synthesize(self, *args: Any, **kwargs: Any) -> Any:
        return None


def _make_cost_tracker() -> MagicMock:
    tracker = MagicMock()
    tracker.create_record = MagicMock(return_value=MagicMock())
    tracker.notify_spend = AsyncMock()
    return tracker


# ---------------------------------------------------------------------------
# isinstance gate — the literal contract LK's agent_activity checks.
# ---------------------------------------------------------------------------


def test_instrumented_stt_isinstance_of_lk_stt() -> None:
    wrapper = InstrumentedSTT(
        wrapped=_RealSTT(),
        model_id="t/m",
        provider="t",
        project="default",
        cost_tracker=_make_cost_tracker(),
        storage=None,
    )
    assert isinstance(wrapper, lk_stt.STT), (
        "agent_activity._start_session lines 669-671 only attach the "
        "metrics_collected listener to STT instances passing this gate. "
        "Without the subclass relation, every STT request would skip "
        "metrics + error event handling under the real LK runtime."
    )


def test_instrumented_llm_isinstance_of_lk_llm() -> None:
    wrapper = InstrumentedLLM(
        wrapped=_RealLLM(),
        model_id="t/m",
        provider="t",
        project="default",
        cost_tracker=_make_cost_tracker(),
        storage=None,
    )
    assert isinstance(wrapper, lk_llm.LLM), (
        "agent_activity._start_session lines 665-667 only attach the "
        "metrics_collected listener to LLM instances passing this gate."
    )


def test_instrumented_tts_isinstance_of_lk_tts() -> None:
    wrapper = InstrumentedTTS(
        wrapped=_RealTTS(),
        model_id="t/m",
        provider="t",
        project="default",
        cost_tracker=_make_cost_tracker(),
        storage=None,
    )
    assert isinstance(wrapper, lk_tts.TTS), (
        "agent_activity._start_session lines 673-675 only attach the "
        "metrics_collected listener to TTS instances passing this gate. "
        "Without it, SpeechHandle never observes speech completion and "
        "the 5s INTERRUPTION_TIMEOUT cancels every speech under real "
        "audio (the AC-2 regression)."
    )


# ---------------------------------------------------------------------------
# Event bridge — the actual unblocker. LK attaches listeners to the
# wrapper; the wrapped plugin emits the events; the bridge must forward.
# ---------------------------------------------------------------------------


def _assert_event_bridge(wrapper: Any, wrapped: Any) -> None:
    """Listener registered on the wrapper receives events emitted on wrapped."""
    received_metrics: list[Any] = []
    received_errors: list[Any] = []
    wrapper.on("metrics_collected", lambda payload: received_metrics.append(payload))
    wrapper.on("error", lambda payload: received_errors.append(payload))

    sentinel_metric = object()
    sentinel_error = object()
    wrapped.emit("metrics_collected", sentinel_metric)
    wrapped.emit("error", sentinel_error)

    assert received_metrics == [sentinel_metric], (
        "metrics_collected listener attached to the wrapper did not "
        "receive the event emitted on the wrapped plugin. The bridge "
        "in _InstrumentedBase._init_instrumentation is broken; "
        "SpeechHandle will never see speech completion."
    )
    assert received_errors == [sentinel_error], (
        "error listener attached to the wrapper did not receive the "
        "event emitted on the wrapped plugin. LK's _on_error path will "
        "not fire and audio errors will go unsurfaced."
    )


def test_stt_event_bridge_forwards_metrics_and_error() -> None:
    wrapped = _RealSTT()
    wrapper = InstrumentedSTT(
        wrapped=wrapped,
        model_id="t/m",
        provider="t",
        project="default",
        cost_tracker=_make_cost_tracker(),
        storage=None,
    )
    _assert_event_bridge(wrapper, wrapped)


def test_llm_event_bridge_forwards_metrics_and_error() -> None:
    wrapped = _RealLLM()
    wrapper = InstrumentedLLM(
        wrapped=wrapped,
        model_id="t/m",
        provider="t",
        project="default",
        cost_tracker=_make_cost_tracker(),
        storage=None,
    )
    _assert_event_bridge(wrapper, wrapped)


def test_tts_event_bridge_forwards_metrics_and_error() -> None:
    wrapped = _RealTTS()
    wrapper = InstrumentedTTS(
        wrapped=wrapped,
        model_id="t/m",
        provider="t",
        project="default",
        cost_tracker=_make_cost_tracker(),
        storage=None,
    )
    _assert_event_bridge(wrapper, wrapped)


# ---------------------------------------------------------------------------
# Capabilities forwarding — LK reads .capabilities/.sample_rate/etc on
# the wrapper for streaming-vs-batch routing decisions. Must come from
# the wrapped instance, not a stub.
# ---------------------------------------------------------------------------


def test_stt_capabilities_forwards_from_wrapped() -> None:
    wrapped = _RealSTT()
    wrapper = InstrumentedSTT(
        wrapped=wrapped,
        model_id="t/m",
        provider="t",
        project="default",
        cost_tracker=_make_cost_tracker(),
        storage=None,
    )
    assert wrapper.capabilities is wrapped.capabilities


def test_tts_capabilities_and_sample_rate_forward_from_wrapped() -> None:
    wrapped = _RealTTS()
    wrapper = InstrumentedTTS(
        wrapped=wrapped,
        model_id="t/m",
        provider="t",
        project="default",
        cost_tracker=_make_cost_tracker(),
        storage=None,
    )
    assert wrapper.capabilities is wrapped.capabilities
    assert wrapper.sample_rate == wrapped.sample_rate
    assert wrapper.num_channels == wrapped.num_channels


def test_label_model_provider_forward_from_wrapped() -> None:
    """The override of label/model/provider should surface the wrapped"""
    wrapped = _RealTTS()
    wrapper = InstrumentedTTS(
        wrapped=wrapped,
        model_id="t/m",
        provider="t",
        project="default",
        cost_tracker=_make_cost_tracker(),
        storage=None,
    )
    assert wrapper.label == wrapped.label
    assert wrapper.model == wrapped.model
    # ``provider`` is also a property on the wrapper. Both flow through
    # to the wrapped's value (which itself defaults to "unknown" until
    # the plugin overrides).
    assert wrapper.provider == wrapped.provider
