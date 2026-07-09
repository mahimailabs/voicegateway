"""Tests for voicegateway.guard() on Pipecat services (control-only).

The Pipecat ``guard()`` is the ACTIVE control seam (fallback / rate-limit /
budget) for pipecat, mirroring the LiveKit guard. It composes with attach() (the
Pipecat ``VoiceGatewayObserver``, the sole meter) and writes NO metrics itself.

These tests use stub Pipecat services (real ``STTService`` / ``LLMService`` /
``TTSService`` subclasses so the isinstance dispatch + ``pipecat_identity`` work)
driven through the guard's control core and its ``FrameProcessor`` wrapper. They
assert:

- rate-limit throttles,
- budget raises ``BudgetExceededError`` when over the window's spend,
- fallback runs the fallback service AND sets ``_current_guard_fallback_from`` so
  the P2 observer would stamp ``fallback_from`` / ``status="fallback"``,
- guard writes NO ``RequestRecord``s itself.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("pipecat")

from pipecat.frames.frames import (  # noqa: E402
    ErrorFrame,
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    StartFrame,
    TextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
)
from pipecat.processors.frame_processor import (  # noqa: E402
    FrameDirection,
    FrameProcessor,
)
from pipecat.services.llm_service import LLMService  # noqa: E402
from pipecat.services.stt_service import STTService  # noqa: E402
from pipecat.services.tts_service import TTSService  # noqa: E402

import voicegateway  # noqa: E402
from voicegateway.inference.session.context import (  # noqa: E402
    current_guard_fallback_from,
    reset_guard_fallback_from,
)
from voicegateway.middleware.budget_enforcer_middleware import (  # noqa: E402
    BudgetExceededError,
)
from voicegateway.middleware.rate_limiter_middleware import (  # noqa: E402
    RateLimitExceeded,
)

# --- stub pipecat services (real subclasses so the dispatch works) ----------

_SUBCLASS_CACHE: dict[tuple[type, str], type] = {}


def _provider_subclass(base: type, provider: str, modality: str) -> type:
    """A per-provider subclass of ``base`` living under a provider-shaped module.

    ``pipecat_identity`` reads ``type(obj).__module__`` to resolve the provider,
    so each stub needs its own ``__module__`` (mutating the shared base class
    would let the last-built provider win). Cached so repeated builds of the same
    (base, provider) reuse one class.
    """
    key = (base, provider)
    cls = _SUBCLASS_CACHE.get(key)
    if cls is None:
        cls = type(f"{base.__name__}_{provider}", (base,), {})
        cls.__module__ = f"pipecat.services.{provider}.{modality}"
        _SUBCLASS_CACHE[key] = cls
    return cls


class _StubLLM(LLMService):
    """A stub pipecat LLM service under a provider-shaped module.

    ``run_inference`` emits a full LLM response (start, text, end) unless it is
    configured to fail, in which case it pushes an ``ErrorFrame`` before any
    output (the "primary failed before producing output" case).
    """

    def __init__(self, *, provider: str, model: str, fail: bool = False) -> None:
        super().__init__()
        # Emulate pipecat.services.<provider>.llm so pipecat_identity resolves.
        # Use a per-instance subclass so setting __module__ does not mutate the
        # shared _StubLLM class (which would make the last-built provider win).
        self.__class__ = _provider_subclass(_StubLLM, provider, "llm")
        self._provider = provider
        self._settings.model = model
        self._fail = fail
        self.request_calls = 0

    async def run_request(self) -> list[Frame]:
        """Produce the response frames for one guarded LLM request.

        Returns the frames the service would push. On failure it returns a lone
        ErrorFrame (no output frames), modeling a primary that errored before
        producing output.
        """
        self.request_calls += 1
        if self._fail:
            return [ErrorFrame(f"{self._provider} llm boom")]
        return [
            LLMFullResponseStartFrame(),
            LLMTextFrame(f"hello from {self._provider}"),
            LLMFullResponseEndFrame(),
        ]


class _StubSTT(STTService):
    def __init__(self, *, provider: str, model: str, fail: bool = False) -> None:
        super().__init__()
        self.__class__ = _provider_subclass(_StubSTT, provider, "stt")
        self._provider = provider
        self._settings.model = model
        self._fail = fail
        self.request_calls = 0

    async def run_stt(self, audio: bytes) -> Any:  # pragma: no cover - not driven
        yield None

    async def run_request(self) -> list[Frame]:
        self.request_calls += 1
        if self._fail:
            return [ErrorFrame(f"{self._provider} stt boom")]
        return [TranscriptionFrame(f"transcript from {self._provider}", "", "")]


class _StubTTS(TTSService):
    def __init__(self, *, provider: str, model: str, fail: bool = False) -> None:
        super().__init__()
        self.__class__ = _provider_subclass(_StubTTS, provider, "tts")
        self._provider = provider
        self._settings.model = model
        self._fail = fail
        self.request_calls = 0

    async def run_tts(self, text: str) -> Any:  # pragma: no cover - not driven
        yield None

    async def run_request(self) -> list[Frame]:
        self.request_calls += 1
        if self._fail:
            return [ErrorFrame(f"{self._provider} tts boom")]
        return [TTSAudioRawFrame(b"\x00\x00", 16000, 1)]


class _RecordingSink:
    def __init__(self) -> None:
        self.rows: list[Any] = []

    async def log_request(self, record: Any) -> None:
        self.rows.append(record)

    async def flush(self) -> None:
        pass

    async def aclose(self) -> None:
        pass


def _is_output(frame: Frame) -> bool:
    """A produced-output frame (vs an error/no-output)."""
    return isinstance(
        frame,
        (
            LLMFullResponseStartFrame,
            LLMTextFrame,
            TranscriptionFrame,
            TTSAudioRawFrame,
        ),
    )


@pytest.fixture(autouse=True)
def _reset_fallback_marker():
    reset_guard_fallback_from()
    yield
    reset_guard_fallback_from()


# --- guard returns a drop-in of the same framework type ---------------------


def test_guard_llm_returns_pipecat_llm_service():
    primary = _StubLLM(provider="openai", model="gpt-4o-mini")
    guarded = voicegateway.guard(primary)
    assert isinstance(guarded, LLMService)


def test_guard_stt_returns_pipecat_stt_service():
    primary = _StubSTT(provider="deepgram", model="nova-3")
    guarded = voicegateway.guard(primary)
    assert isinstance(guarded, STTService)


def test_guard_tts_returns_pipecat_tts_service():
    primary = _StubTTS(provider="cartesia", model="sonic")
    guarded = voicegateway.guard(primary)
    assert isinstance(guarded, TTSService)


def test_guard_llm_wrapper_is_a_frame_processor():
    primary = _StubLLM(provider="openai", model="gpt-4o-mini")
    guarded = voicegateway.guard(primary)
    assert isinstance(guarded, FrameProcessor)


# --- rate limit -------------------------------------------------------------


async def test_guard_rate_limit_throttles():
    """A 1/min bucket admits the first request and rejects the second."""
    primary = _StubLLM(provider="openai", model="gpt-4o-mini")
    guarded = voicegateway.guard(primary, rate_limit="1/min")
    control = guarded._control

    await control.preflight()  # first request consumes the token
    with pytest.raises(RateLimitExceeded):
        await control.preflight()  # bucket empty


async def test_guard_rate_limit_none_never_throttles():
    primary = _StubTTS(provider="cartesia", model="sonic")
    guarded = voicegateway.guard(primary)
    for _ in range(10):
        await guarded._control.preflight()


async def test_guard_wrapper_runs_preflight_on_request_boundary_frame():
    """The FrameProcessor wrapper enforces the gates inline: processing a TTS
    request-boundary frame (TextFrame) runs preflight, so a 1/min bucket lets the
    first through and raises on the second, while a non-boundary frame does not
    consume the bucket."""
    primary = _StubTTS(provider="cartesia", model="sonic")
    guarded = voicegateway.guard(primary, rate_limit="1/min")

    forwarded: list[Frame] = []

    async def _fake_super(frame: Frame, direction: FrameDirection) -> None:
        forwarded.append(frame)

    # A non-boundary frame passes through without consuming the bucket.
    await guarded._guard_process(
        StartFrame(), FrameDirection.DOWNSTREAM, super_process=_fake_super
    )
    # First request frame: consumes the single token, forwards.
    await guarded._guard_process(
        TextFrame("say hi"), FrameDirection.DOWNSTREAM, super_process=_fake_super
    )
    # Second request frame within the minute: bucket empty -> raises.
    with pytest.raises(RateLimitExceeded):
        await guarded._guard_process(
            TextFrame("again"), FrameDirection.DOWNSTREAM, super_process=_fake_super
        )
    # The StartFrame and the first TextFrame were forwarded; the throttled one was
    # not (preflight raised before forwarding).
    assert forwarded == [forwarded[0], forwarded[1]]
    assert isinstance(forwarded[0], StartFrame)
    assert isinstance(forwarded[1], TextFrame)


# --- budget -----------------------------------------------------------------


async def test_guard_budget_blocks_when_over_window_spend():
    primary = _StubLLM(provider="openai", model="gpt-4o-mini")

    async def _spend_reader(project: str, period: str) -> float:
        return 6.0  # already spent $6 today; cap is $5/day

    from voicegateway.inference.pipecat.guard_pipecat import guard_pipecat

    guarded = guard_pipecat(primary, budget="$5.00/day", spend_reader=_spend_reader)
    with pytest.raises(BudgetExceededError):
        await guarded._control.preflight()


async def test_guard_budget_allows_when_under_window_spend():
    primary = _StubLLM(provider="openai", model="gpt-4o-mini")

    async def _spend_reader(project: str, period: str) -> float:
        return 1.0  # under the $5 cap

    from voicegateway.inference.pipecat.guard_pipecat import guard_pipecat

    guarded = guard_pipecat(primary, budget="$5.00/day", spend_reader=_spend_reader)
    await guarded._control.preflight()  # does not raise


# --- fallback ---------------------------------------------------------------


async def test_guard_fallback_runs_secondary_on_primary_error():
    """When the primary produces an ErrorFrame before output, the fallback runs."""
    primary = _StubLLM(provider="openai", model="gpt-4o-mini", fail=True)
    backup = _StubLLM(provider="anthropic", model="claude-haiku", fail=False)
    guarded = voicegateway.guard(primary, fallback=[backup])

    async def _invoke(service: Any) -> list[Frame]:
        return await service.run_request()

    frames = await guarded._control.run_request_with_fallback(
        _invoke, produced_output=_is_output
    )
    # The fallback produced the output.
    assert any(isinstance(f, LLMTextFrame) for f in frames)
    text = next(f for f in frames if isinstance(f, LLMTextFrame))
    assert text.text == "hello from anthropic"
    assert backup.request_calls == 1


async def test_guard_fallback_stt_runs_secondary():
    primary = _StubSTT(provider="deepgram", model="nova-3", fail=True)
    backup = _StubSTT(provider="assemblyai", model="best", fail=False)
    guarded = voicegateway.guard(primary, fallback=[backup])

    async def _invoke(service: Any) -> list[Frame]:
        return await service.run_request()

    frames = await guarded._control.run_request_with_fallback(
        _invoke, produced_output=_is_output
    )
    tr = next(f for f in frames if isinstance(f, TranscriptionFrame))
    assert tr.text == "transcript from assemblyai"


async def test_guard_all_providers_fail_returns_error_frames():
    """When every service fails, the last error is surfaced (no output)."""
    primary = _StubLLM(provider="openai", model="gpt-4o-mini", fail=True)
    backup = _StubLLM(provider="anthropic", model="claude-haiku", fail=True)
    guarded = voicegateway.guard(primary, fallback=[backup])

    async def _invoke(service: Any) -> list[Frame]:
        return await service.run_request()

    frames = await guarded._control.run_request_with_fallback(
        _invoke, produced_output=_is_output
    )
    # No output frame was produced; the error frames are surfaced.
    assert not any(_is_output(f) for f in frames)
    assert any(isinstance(f, ErrorFrame) for f in frames)


async def test_guard_fallback_uses_builtin_output_predicate():
    """Without an explicit predicate, the control uses the modality one it was
    built with (LLMTextFrame counts as output), so the fallback still runs."""
    primary = _StubLLM(provider="openai", model="gpt-4o-mini", fail=True)
    backup = _StubLLM(provider="anthropic", model="claude-haiku", fail=False)
    guarded = voicegateway.guard(primary, fallback=[backup])

    async def _invoke(service: Any) -> list[Frame]:
        return await service.run_request()

    frames = await guarded._control.run_request_with_fallback(_invoke)
    text = next(f for f in frames if isinstance(f, LLMTextFrame))
    assert text.text == "hello from anthropic"


async def test_guard_fallback_sets_contextvar_for_attach_stamp():
    """After a fallback, the guard ContextVar carries the primary provider so the
    observer stamps fallback_from on the record it writes."""
    primary = _StubLLM(provider="openai", model="gpt-4o-mini", fail=True)
    backup = _StubLLM(provider="anthropic", model="claude-haiku", fail=False)
    guarded = voicegateway.guard(primary, fallback=[backup])

    async def _invoke(service: Any) -> list[Frame]:
        return await service.run_request()

    await guarded._control.run_request_with_fallback(
        _invoke, produced_output=_is_output
    )
    # The marker is the primary provider that was fallen back FROM.
    assert current_guard_fallback_from() == "openai"


async def test_guard_no_fallback_clears_marker_on_success():
    """A successful primary run leaves no stale fallback marker."""
    from voicegateway.inference.session.context import set_guard_fallback_from

    set_guard_fallback_from("stale")
    primary = _StubLLM(provider="openai", model="gpt-4o-mini", fail=False)
    guarded = voicegateway.guard(primary)

    async def _invoke(service: Any) -> list[Frame]:
        return await service.run_request()

    await guarded._control.run_request_with_fallback(
        _invoke, produced_output=_is_output
    )
    assert current_guard_fallback_from() is None


# --- observer stamps fallback_from after guard sets the ContextVar ----------


async def test_observer_stamps_fallback_from_after_guard_fallback():
    """End-to-end coordination: guard sets the ContextVar; the P2 observer stamps
    fallback_from + status='fallback' on the record it writes for the service
    that actually ran."""
    from pipecat.frames.frames import MetricsFrame
    from pipecat.metrics.metrics import LLMTokenUsage, LLMUsageMetricsData

    sink = _RecordingSink()
    observer = voicegateway.Observer(sink=sink, project="p", agent_id="a")

    # The fallback service that actually ran (anthropic).
    ran = _StubLLM(provider="anthropic", model="claude-haiku")

    # Guard fell back from openai to anthropic for this request.
    primary = _StubLLM(provider="openai", model="gpt-4o-mini", fail=True)
    guarded = voicegateway.guard(primary, fallback=[ran])

    async def _invoke(service: Any) -> list[Frame]:
        return await service.run_request()

    await guarded._control.run_request_with_fallback(
        _invoke, produced_output=_is_output
    )
    assert current_guard_fallback_from() == "openai"

    # Now the observer meters the run that anthropic produced.
    usage = LLMTokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    md = LLMUsageMetricsData(processor=ran.name, model="claude-haiku", value=usage)

    class _Pushed:
        frame = MetricsFrame(data=[md])
        source = ran

    await observer.on_push_frame(_Pushed())

    assert len(sink.rows) == 1
    row = sink.rows[0]
    assert row.provider == "anthropic"
    assert row.fallback_from == "openai"
    assert row.status == "fallback"


# --- guard writes NO metrics itself -----------------------------------------


async def test_guard_writes_no_records():
    """Driving a guarded pipecat request writes zero rows through the guard."""
    sink = _RecordingSink()
    primary = _StubLLM(provider="openai", model="gpt-4o-mini")
    guarded = voicegateway.guard(primary)

    async def _invoke(service: Any) -> list[Frame]:
        return await service.run_request()

    await guarded._control.run_request_with_fallback(
        _invoke, produced_output=_is_output
    )
    # The guard holds no sink and never meters.
    assert sink.rows == []
    assert not hasattr(guarded, "_sink")


# --- unknown provider -------------------------------------------------------


def test_guard_rejects_non_service_frame_processor():
    """A bare FrameProcessor that is not an STT/LLM/TTS service is rejected."""
    fp = FrameProcessor()
    type(fp).__module__ = "pipecat.processors.frame_processor"
    with pytest.raises((ValueError, TypeError)):
        voicegateway.guard(fp)
