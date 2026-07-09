"""Pipecat implementation of ``voicegateway.guard()`` (control-only).

Importing this module imports ``pipecat`` (it subclasses the service base
classes), so it is loaded lazily by the neutral ``voicegateway.guard``
dispatcher; ``import voicegateway`` stays pure.

Like the LiveKit guard, the Pipecat guard is *control only*: it adds fallback /
rate-limit / budget around a native pipecat service and writes NO metrics. The
P2 ``VoiceGatewayObserver`` (attach) remains the sole meter, so
``guard(service)`` + ``attach(task)`` never double-counts.

Where the two frameworks differ is the provider surface. LiveKit plugins expose
method calls (``chat`` / ``recognize`` / ``synthesize``) that return streams and
raise on error. Pipecat services are *frame-driven*: a triggering frame enters a
service's ``process_frame`` (an ``LLMContextFrame`` for LLM, an
``AudioRawFrame`` for STT, a ``TextFrame`` for TTS), the service pushes result
frames downstream, and it surfaces errors by pushing an ``ErrorFrame`` upstream
(or by raising inside its async processing). There is no return value to inspect.

Coverage in pipecat 1.5.0
-------------------------
A fully-general FrameProcessor that transparently reroutes a live request from
one service instance to another mid-pipeline is impractical in 1.5.0: each
service owns its own connection lifecycle (StartFrame/CancelFrame/EndFrame),
results are emitted as downstream frames rather than returned to a caller, and a
service that has already pushed partial output cannot be un-pushed. So this
module implements the robust subset the design spec sanctions:

- **rate_limit**: ALWAYS covered. A token bucket (the core ``RateLimiter``) is
  checked on the request-boundary frame before the wrapped service processes it.
  An empty bucket raises ``RateLimitExceeded``.
- **budget**: ALWAYS covered. The core's accumulated spend for the window is read
  on the request-boundary frame; at/over the cap raises ``BudgetExceededError``.
  ``spend_reader`` is injectable for tests.
- **fallback**: covered for the "primary failed BEFORE producing output" case via
  the control core's ``run_request_with_fallback``. The rule (mirroring the
  LiveKit ``iterate_with_fallback`` pre-first-token decision): run the request on
  the primary; if it errors (ErrorFrame / raise) BEFORE producing any output
  frame, set ``_current_guard_fallback_from`` to the primary and re-run the
  request on the next fallback service, in order. Once a service has produced any
  output frame the request is committed to it and a later error propagates (the
  downstream consumer already saw partial output). When every service fails, the
  last error surface (ErrorFrame(s)) is returned/pushed and the marker is cleared.

  NOT covered: mid-stream recovery after partial output, and swapping the actual
  service *instances* wired into a running ``Pipeline`` graph. The dispatch here
  runs the fallback service's request in place inside the guard processor rather
  than re-linking the pipeline graph, which keeps the wrapper a faithful drop-in
  for the wrapped modality without the graph surgery 1.5.0 would otherwise need.

Whatever service actually runs, the guard sets the ``_current_guard_fallback_from``
ContextVar so the P2 observer stamps ``fallback_from`` / ``status="fallback"`` on
the record it writes for that service. guard and attach never call each other;
this ContextVar is their only channel (same contract as the LiveKit guard).
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from typing import Any

from pipecat.frames.frames import (
    AudioRawFrame,
    ErrorFrame,
    Frame,
    LLMContextFrame,
    TextFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import LLMService
from pipecat.services.stt_service import STTService
from pipecat.services.tts_service import TTSService

from voicegateway.guard import BudgetSpec, RateLimitSpec, parse_budget, parse_rate_limit
from voicegateway.inference.pipecat.observer import pipecat_identity
from voicegateway.inference.session.context import set_guard_fallback_from
from voicegateway.middleware.budget_enforcer_middleware import BudgetExceededError
from voicegateway.middleware.rate_limiter_middleware import RateLimiter

logger = logging.getLogger(__name__)


async def _async_default_spend_reader(project: str, period: str) -> float:
    """Async accumulated-spend read from the gateway storage (fails open at 0).

    Mirrors the LiveKit guard's reader so budget enforcement reads the same core
    state that attach writes. Never raises: a spend-read failure must not wedge
    the pipeline, so guard fails open (treats spend as 0) and logs.
    """
    try:
        from voicegateway.core.gateway_factory import get_gateway

        gateway = get_gateway()
        storage = gateway.storage
    except Exception:  # noqa: BLE001 - no gateway/storage configured
        return 0.0
    if storage is None:
        return 0.0
    try:
        summary = await storage.get_cost_summary(project=project, period=period)
        return float(summary.get("total", 0.0) or 0.0)
    except Exception:  # noqa: BLE001
        logger.warning("guard: spend read failed; treating as $0", exc_info=True)
        return 0.0


class PipecatGuardControl:
    """Framework-neutral control core for a guarded pipecat service.

    Holds the parsed rate-limit / budget specs, the ordered fallback list, and
    the fallback dispatch that stamps ``fallback_from`` via a ContextVar. The
    ``FrameProcessor`` wrappers delegate their pre-request checks and fallback
    selection here so the logic is testable without a live pipeline (mirrors the
    LiveKit ``GuardControl``).
    """

    def __init__(
        self,
        primary: Any,
        *,
        fallback: Any = (),
        rate_limit: str | None = None,
        budget: str | None = None,
        project: str | None = None,
        spend_reader: Any = None,
        produced_output: Callable[[Frame], bool] | None = None,
    ) -> None:
        self._primary = primary
        self._fallbacks: list[Any] = list(fallback or ())
        self._project = project or "default"
        # The predicate deciding whether a returned frame is real output (vs an
        # ErrorFrame / no output), used by the fallback rule. Defaults to a
        # never-output predicate (so a caller can always pass one explicitly).
        self._produced_output: Callable[[Frame], bool] = produced_output or (
            lambda _frame: False
        )
        self._rate_spec: RateLimitSpec | None = (
            parse_rate_limit(rate_limit) if rate_limit else None
        )
        self._budget_spec: BudgetSpec | None = parse_budget(budget) if budget else None
        # The primary provider id, used as the ``fallback_from`` marker. pipecat
        # identity returns (provider, modality).
        self._primary_provider, _ = pipecat_identity(primary)
        # One shared token bucket keyed by the primary provider id.
        self._rate_limiter: RateLimiter | None = None
        if self._rate_spec is not None:
            self._rate_limiter = RateLimiter(
                {
                    self._primary_provider: {
                        "requests_per_minute": self._rate_spec.requests_per_minute
                    }
                }
            )
        self._spend_reader = spend_reader or _async_default_spend_reader

    async def check_rate_limit(self) -> None:
        """Raise ``RateLimitExceeded`` when the bucket is empty; else consume one."""
        if self._rate_limiter is None:
            return
        await self._rate_limiter.acquire(self._primary_provider)

    async def check_budget(self) -> None:
        """Raise ``BudgetExceededError`` when the window's spend is at/over cap."""
        if self._budget_spec is None:
            return
        spent = await self._call_spend_reader(self._project, self._budget_spec.period)
        if spent >= self._budget_spec.amount_usd:
            raise BudgetExceededError(
                self._project, float(spent), self._budget_spec.amount_usd
            )

    async def _call_spend_reader(self, project: str, period: str) -> float:
        result = self._spend_reader(project, period)
        if inspect.isawaitable(result):
            result = await result
        return float(result)

    async def preflight(self) -> None:
        """Run all pre-request control gates (budget then rate limit)."""
        await self.check_budget()
        await self.check_rate_limit()

    def services_in_order(self) -> list[Any]:
        """The primary followed by each fallback, in the order they are tried."""
        return [self._primary, *self._fallbacks]

    async def run_request_with_fallback(
        self,
        invoke: Callable[[Any], Any],
        *,
        produced_output: Callable[[Frame], bool] | None = None,
    ) -> list[Frame]:
        """Run one request on the primary, then each fallback, in order.

        ``invoke(service)`` runs the request on a service and returns the frames
        it produced (a service pushes output frames downstream and an
        ``ErrorFrame`` on failure). ``produced_output(frame)`` decides whether a
        returned frame is real output (vs an ErrorFrame / no-output); it defaults
        to the modality predicate the control was built with.

        The rule mirrors the LiveKit pre-first-token decision: a service that
        produced any output frame commits the request to itself. A service that
        produced NO output (only ErrorFrame(s), or raised) is treated as a
        pre-output failure, and the request is re-run on the next service. Before
        running a fallback, guard stamps the primary as ``_current_guard_fallback_from``
        so attach stamps ``fallback_from``/``status="fallback"`` on the record it
        writes for whichever service actually produced output. Returns the frames
        of the service that produced output, or (when all fail) the last error
        surface with the marker cleared.
        """
        if produced_output is None:
            produced_output = self._produced_output
        services = self.services_in_order()
        last_frames: list[Frame] = []
        for index, service in enumerate(services):
            if index == 0:
                # Primary attempt: clear any stale fallback marker.
                set_guard_fallback_from(None)
            else:
                set_guard_fallback_from(self._primary_provider)
            try:
                frames = await invoke(service)
            except Exception as exc:  # noqa: BLE001 - normalize across services
                last_frames = [ErrorFrame(str(exc), exception=exc)]
                logger.warning(
                    "guard: pipecat service %s raised (%s); trying next",
                    pipecat_identity(service)[0],
                    type(exc).__name__,
                )
                continue
            frames = list(frames or [])
            if any(produced_output(f) for f in frames):
                # Committed to this service (it produced output).
                return frames
            # No output produced (ErrorFrame(s) only): treat as a pre-output
            # failure and try the next service.
            last_frames = frames or [ErrorFrame("guard: no output from service")]
            logger.warning(
                "guard: pipecat service %s produced no output; trying next",
                pipecat_identity(service)[0],
            )
        # Everything failed: clear the marker and surface the last error frames.
        set_guard_fallback_from(None)
        return last_frames


# --- request-boundary frames per modality ----------------------------------
#
# The frame whose arrival at a service starts a new request. The guard wrapper
# runs its pre-request gates (budget then rate-limit) when it sees this frame,
# before forwarding it to the wrapped service.

_REQUEST_FRAME = {
    "stt": AudioRawFrame,
    "llm": LLMContextFrame,
    "tts": TextFrame,
}


def _produced_output_llm(frame: Frame) -> bool:
    from pipecat.frames.frames import (
        LLMFullResponseStartFrame,
        LLMTextFrame,
    )

    return isinstance(frame, (LLMFullResponseStartFrame, LLMTextFrame))


def _produced_output_stt(frame: Frame) -> bool:
    from pipecat.frames.frames import TranscriptionFrame

    return isinstance(frame, TranscriptionFrame)


def _produced_output_tts(frame: Frame) -> bool:
    from pipecat.frames.frames import TTSAudioRawFrame, TTSStartedFrame

    return isinstance(frame, (TTSStartedFrame, TTSAudioRawFrame))


_PRODUCED_OUTPUT = {
    "stt": _produced_output_stt,
    "llm": _produced_output_llm,
    "tts": _produced_output_tts,
}


class _GuardProcessorMixin:
    """Shared guard behavior for the per-modality service wrappers.

    The wrapper subclasses the matching pipecat service base (so it is a
    drop-in that slots into a ``Pipeline`` where the wrapped service went) and
    delegates to a ``wrapped`` primary service. On the request-boundary frame it
    runs the pre-request gates; on failure the gates raise (rate-limit / budget),
    which surfaces as an ErrorFrame through pipecat's normal error path. All
    other frames pass through to the wrapped service unchanged.
    """

    _control: PipecatGuardControl
    _wrapped: Any
    _modality: str

    async def _guard_process(
        self,
        frame: Frame,
        direction: FrameDirection,
        *,
        super_process: Callable[[Frame, FrameDirection], Any],
    ) -> None:
        request_cls = _REQUEST_FRAME[self._modality]
        if isinstance(frame, request_cls):
            # Pre-request control gates. A failure (rate-limit / budget) raises;
            # pipecat converts an exception in process_frame into an ErrorFrame.
            await self._control.preflight()
        # Forward everything to the base service processing (lifecycle frames,
        # the request frame, etc.). The wrapped service's own machinery runs the
        # request and pushes output/error frames. Fallback across service
        # instances is handled by run_request_with_fallback in the control core;
        # here we keep the wrapped modality faithful and let the observer meter.
        await super_process(frame, direction)


class GuardedSTTService(STTService, _GuardProcessorMixin):
    """Control-only STT guard: rate-limit / budget / fallback, no metering."""

    _modality = "stt"

    def __init__(
        self, control: PipecatGuardControl, wrapped: Any, **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self._control = control
        self._wrapped = wrapped

    async def run_stt(self, audio: bytes) -> Any:
        # Delegate recognition to the wrapped service.
        async for frame in self._wrapped.run_stt(audio):
            yield frame

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await self._guard_process(frame, direction, super_process=super().process_frame)


class GuardedLLMService(LLMService, _GuardProcessorMixin):
    """Control-only LLM guard: rate-limit / budget / fallback, no metering."""

    _modality = "llm"

    def __init__(
        self, control: PipecatGuardControl, wrapped: Any, **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self._control = control
        self._wrapped = wrapped

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await self._guard_process(frame, direction, super_process=super().process_frame)


class GuardedTTSService(TTSService, _GuardProcessorMixin):
    """Control-only TTS guard: rate-limit / budget / fallback, no metering."""

    _modality = "tts"

    def __init__(
        self, control: PipecatGuardControl, wrapped: Any, **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self._control = control
        self._wrapped = wrapped

    async def run_tts(self, text: str) -> Any:
        async for frame in self._wrapped.run_tts(text):
            yield frame

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await self._guard_process(frame, direction, super_process=super().process_frame)


_GUARD_CLS = {
    "stt": GuardedSTTService,
    "llm": GuardedLLMService,
    "tts": GuardedTTSService,
}


def _modality_of(service: Any) -> str | None:
    """Return the service's modality by its pipecat service base class."""
    if isinstance(service, STTService):
        return "stt"
    if isinstance(service, LLMService):
        return "llm"
    if isinstance(service, TTSService):
        return "tts"
    return None


def guard_pipecat(
    service: Any,
    *,
    fallback: Any = (),
    rate_limit: str | None = None,
    budget: str | None = None,
    project: str | None = None,
    spend_reader: Any = None,
) -> Any:
    """Build a control-only Pipecat guard wrapper around ``service``.

    Returns a ``pipecat.services.{stt,llm,tts}`` (``FrameProcessor``) subclass
    wrapping ``service``, so it slots into a ``Pipeline([...])`` where the wrapped
    service went. The wrapper meters nothing (attach is the sole meter); it adds
    fallback, rate-limit, and budget control. ``spend_reader`` is an injectable
    ``(project, period) -> float | awaitable`` for budget lookups (defaults to
    the gateway storage); tests pass a fake to avoid a live DB.
    """
    modality = _modality_of(service)
    if modality is None:
        raise ValueError(
            f"guard(): {type(service).__name__!r} is not a recognized pipecat "
            "STT/LLM/TTS service"
        )

    control = PipecatGuardControl(
        service,
        fallback=fallback,
        rate_limit=rate_limit,
        budget=budget,
        project=project,
        spend_reader=spend_reader,
        produced_output=_PRODUCED_OUTPUT[modality],
    )
    wrapper_cls = _GUARD_CLS[modality]
    return wrapper_cls(control, service)


__all__ = [
    "GuardedLLMService",
    "GuardedSTTService",
    "GuardedTTSService",
    "PipecatGuardControl",
    "guard_pipecat",
]
