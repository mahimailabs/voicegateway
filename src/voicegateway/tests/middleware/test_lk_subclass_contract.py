"""Pin the LK-subclass contract that unblocks AC-2."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from livekit.agents import llm as lk_llm
from livekit.agents import stt as lk_stt
from livekit.agents import tts as lk_tts
from livekit.agents.stt import STTCapabilities
from livekit.agents.tts import TTSCapabilities

from voicegateway.middleware.instrumented_provider_middleware import (
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
# isinstance gate + event bridge, per modality.
#
# LK's agent_activity._start_session only attaches metrics_collected listeners
# to instances passing isinstance(x, lk_stt.STT / lk_llm.LLM / lk_tts.TTS).
# Without that gate, SpeechHandle never observes speech completion and the 5s
# INTERRUPTION_TIMEOUT cancels every speech (the AC-2 regression). The bridge
# must then forward events emitted on the wrapped plugin to listeners attached
# to the wrapper.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("wrapper_cls", "fake_cls", "lk_base"),
    [
        (InstrumentedSTT, _RealSTT, lk_stt.STT),
        (InstrumentedLLM, _RealLLM, lk_llm.LLM),
        (InstrumentedTTS, _RealTTS, lk_tts.TTS),
    ],
)
def test_wrapper_is_lk_instance_and_forwards_metrics_and_error(
    wrapper_cls: type, fake_cls: type, lk_base: type
) -> None:
    wrapped = fake_cls()
    wrapper = wrapper_cls(
        wrapped=wrapped,
        model_id="t/m",
        provider="t",
        project="default",
        cost_tracker=_make_cost_tracker(),
        storage=None,
    )
    assert isinstance(wrapper, lk_base)

    received_metrics: list[Any] = []
    received_errors: list[Any] = []
    wrapper.on("metrics_collected", lambda payload: received_metrics.append(payload))
    wrapper.on("error", lambda payload: received_errors.append(payload))

    sentinel_metric = object()
    sentinel_error = object()
    wrapped.emit("metrics_collected", sentinel_metric)
    wrapped.emit("error", sentinel_error)

    assert received_metrics == [sentinel_metric]
    assert received_errors == [sentinel_error]


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
