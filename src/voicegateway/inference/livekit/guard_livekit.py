"""LiveKit implementation of ``voicegateway.guard()`` (control-only).

Importing this module imports ``livekit.agents`` (it subclasses the plugin base
classes), so it is loaded lazily by the neutral ``voicegateway.guard`` dispatcher.

The guard wrappers are the ``Instrumented{STT,LLM,TTS}`` control shells built with
``metering=False``: they subclass the matching ``livekit.agents`` type (so they
drop into an ``AgentSession``), forward the inner plugin's ``metrics_collected``
events transparently (so ``attach()`` meters them once), and write NO records
themselves. On top of that shell they add the control layer:

- **rate_limit**: an async token-bucket check before the call (core RateLimiter).
- **budget**: an async accumulated-spend check before the call (core spend read).
- **fallback**: on a primary-provider error, try each fallback in order, setting
  the guard ContextVar so ``attach`` stamps ``fallback_from`` + ``status="fallback"``
  on the row for the provider that actually ran.
"""

from __future__ import annotations

import logging
from typing import Any

from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN

from voicegateway.guard import BudgetSpec, RateLimitSpec, parse_budget, parse_rate_limit
from voicegateway.inference.session.capture import component_identity
from voicegateway.inference.session.context import (
    set_guard_fallback_from,
)
from voicegateway.middleware.budget_enforcer_middleware import BudgetExceededError
from voicegateway.middleware.cost_tracker_middleware import CostTracker
from voicegateway.middleware.instrumented_provider_middleware import (
    InstrumentedLLM,
    InstrumentedSTT,
    InstrumentedTTS,
)
from voicegateway.middleware.rate_limiter_middleware import RateLimiter

logger = logging.getLogger(__name__)


def _default_spend_reader(project: str, period: str) -> float:
    """Read accumulated spend for ``project`` over ``period`` from the gateway.

    Returns 0.0 when there is no configured storage (nothing to enforce
    against). Never raises: a spend-read failure must not wedge the call path,
    so guard fails open (treats spend as 0) and logs.
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
        import asyncio

        summary = asyncio.run(storage.get_cost_summary(period, project=project))
        return float(summary.get("total", 0.0) or 0.0)
    except RuntimeError:
        # Already inside a running loop; the async control path uses the async
        # reader instead. This sync path is a best-effort fallback only.
        return 0.0
    except Exception:  # noqa: BLE001
        logger.warning("guard: spend read failed; treating as $0", exc_info=True)
        return 0.0


async def _async_default_spend_reader(project: str, period: str) -> float:
    """Async accumulated-spend read from the gateway storage (fails open at 0)."""
    try:
        from voicegateway.core.gateway_factory import get_gateway

        gateway = get_gateway()
        storage = gateway.storage
    except Exception:  # noqa: BLE001
        return 0.0
    if storage is None:
        return 0.0
    try:
        summary = await storage.get_cost_summary(project=project, period=period)
        return float(summary.get("total", 0.0) or 0.0)
    except Exception:  # noqa: BLE001
        logger.warning("guard: spend read failed; treating as $0", exc_info=True)
        return 0.0


class GuardControl:
    """Framework-neutral control core for a guarded provider.

    Holds the parsed rate-limit / budget specs, the ordered fallback list, and
    the coordination that stamps ``fallback_from`` via a ContextVar. The LiveKit
    wrappers delegate their pre-call checks and fallback selection here so the
    logic is testable without a live provider.
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
    ) -> None:
        self._primary = primary
        self._fallbacks: list[Any] = list(fallback or ())
        self._project = project or "default"
        self._rate_spec: RateLimitSpec | None = (
            parse_rate_limit(rate_limit) if rate_limit else None
        )
        self._budget_spec: BudgetSpec | None = parse_budget(budget) if budget else None
        # The primary provider id, used as the ``fallback_from`` marker.
        self._primary_provider, _ = component_identity(primary)
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
        import inspect

        result = self._spend_reader(project, period)
        if inspect.isawaitable(result):
            result = await result
        return float(result)

    async def preflight(self) -> None:
        """Run all pre-call control gates (budget then rate limit)."""
        await self.check_budget()
        await self.check_rate_limit()

    def providers_in_order(self) -> list[Any]:
        """The primary followed by each fallback, in the order they are tried."""
        return [self._primary, *self._fallbacks]

    async def run_with_fallback(self, invoke: Any) -> Any:
        """Run ``invoke(provider)`` on the primary, then each fallback in order.

        ``invoke`` is an async callable taking a provider and returning the
        provider's result. On the primary's error, guard stamps the primary as
        ``fallback_from`` (via the ContextVar attach reads) BEFORE running the
        next provider, so the row attach writes for the provider that succeeds
        carries ``fallback_from=<primary>`` + ``status="fallback"``. Re-raises the
        last error when every provider fails.
        """
        providers = self.providers_in_order()
        last_error: Exception | None = None
        for index, provider in enumerate(providers):
            if index == 0:
                # Primary attempt: clear any stale fallback marker.
                set_guard_fallback_from(None)
            else:
                set_guard_fallback_from(self._primary_provider)
            try:
                return await invoke(provider)
            except Exception as exc:  # noqa: BLE001 - normalize across providers
                last_error = exc
                logger.warning(
                    "guard: provider %s failed (%s); trying next",
                    component_identity(provider)[0],
                    type(exc).__name__,
                )
                continue
        # Everything failed: clear the marker and re-raise the last error.
        set_guard_fallback_from(None)
        if last_error is not None:
            raise last_error
        raise RuntimeError("guard: no providers to run")

    async def iterate_with_fallback(self, open_stream: Any) -> Any:
        """Yield items from the primary stream; fall back on a PRE-FIRST-TOKEN error.

        LK LLM/TTS surface provider errors during iteration, not when the stream
        object is created. So for each provider (primary, then fallbacks), guard
        opens the stream via ``open_stream(provider)`` and starts iterating. If an
        error occurs BEFORE any item is yielded, guard moves to the next provider
        (stamping the primary as ``fallback_from`` for attach). Once an item has
        been yielded, an error can no longer be recovered and propagates: the
        downstream consumer already saw partial output.
        """
        providers = self.providers_in_order()
        last_error: Exception | None = None
        for index, provider in enumerate(providers):
            if index == 0:
                set_guard_fallback_from(None)
            else:
                set_guard_fallback_from(self._primary_provider)
            yielded_any = False
            try:
                stream = open_stream(provider)
                async for item in stream:
                    yielded_any = True
                    yield item
                return
            except Exception as exc:  # noqa: BLE001 - normalize across providers
                if yielded_any:
                    # Partial output already delivered; cannot recover.
                    set_guard_fallback_from(None)
                    raise
                last_error = exc
                logger.warning(
                    "guard: provider %s stream failed before first token (%s); "
                    "trying next",
                    component_identity(provider)[0],
                    type(exc).__name__,
                )
                continue
        set_guard_fallback_from(None)
        if last_error is not None:
            raise last_error
        raise RuntimeError("guard: no providers to run")


# --- LiveKit wrappers ------------------------------------------------------
#
# Each subclasses the metering shell with ``metering=False`` (attach is the sole
# meter) and layers the control core over the primary call method.


def _noop_cost_tracker() -> CostTracker:
    """A CostTracker with no storage; the shell needs one but never meters here."""
    return CostTracker(storage=None)


class GuardedSTT(InstrumentedSTT):
    """Control-only STT guard: rate-limit / budget / fallback, no metering."""

    def __init__(self, control: GuardControl, **kwargs: Any) -> None:
        super().__init__(metering=False, **kwargs)
        self._control = control

    async def _recognize_impl(
        self,
        buffer: Any,
        *,
        language: Any = NOT_GIVEN,
        conn_options: Any = DEFAULT_API_CONNECT_OPTIONS,
    ) -> Any:
        await self._control.preflight()

        async def _invoke(provider: Any) -> Any:
            return await provider._recognize_impl(
                buffer, language=language, conn_options=conn_options
            )

        return await self._control.run_with_fallback(_invoke)

    async def recognize(
        self,
        buffer: Any,
        *,
        language: Any = NOT_GIVEN,
        conn_options: Any = DEFAULT_API_CONNECT_OPTIONS,
    ) -> Any:
        await self._control.preflight()

        async def _invoke(provider: Any) -> Any:
            return await provider.recognize(
                buffer, language=language, conn_options=conn_options
            )

        return await self._control.run_with_fallback(_invoke)


class GuardedLLM(InstrumentedLLM):
    """Control-only LLM guard: rate-limit / budget / fallback, no metering."""

    def __init__(self, control: GuardControl, **kwargs: Any) -> None:
        super().__init__(metering=False, **kwargs)
        self._control = control

    def chat(
        self,
        *,
        chat_ctx: Any,
        tools: Any = None,
        conn_options: Any = DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls: Any = NOT_GIVEN,
        tool_choice: Any = NOT_GIVEN,
        extra_kwargs: Any = NOT_GIVEN,
    ) -> Any:
        # LK's chat() returns a stream synchronously and surfaces errors during
        # iteration, so control (async preflight + fallback) runs inside a
        # guard stream wrapper that performs the checks before delegating.
        return _GuardLLMStream(
            control=self._control,
            chat_ctx=chat_ctx,
            tools=tools,
            conn_options=conn_options,
            parallel_tool_calls=parallel_tool_calls,
            tool_choice=tool_choice,
            extra_kwargs=extra_kwargs,
        )


class _GuardLLMStream:
    """Lazy guard stream: runs preflight + fallback on first iteration.

    Duck-typed to LK's async-iterable LLM stream. The heavy control work runs
    once, when the caller starts consuming the stream, so a synchronous
    ``chat()`` return does not need to block on the async gates.
    """

    def __init__(self, *, control: GuardControl, **chat_kwargs: Any) -> None:
        self._control = control
        self._chat_kwargs = chat_kwargs

    def __aiter__(self) -> Any:
        return self._agen()

    async def _agen(self) -> Any:
        # Rate-limit / budget gates run once, before any provider is opened.
        await self._control.preflight()

        def _open(provider: Any) -> Any:
            return provider.chat(**self._chat_kwargs)

        async for item in self._control.iterate_with_fallback(_open):
            yield item

    async def aclose(self) -> None:
        return None

    async def __aenter__(self) -> Any:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()


class GuardedTTS(InstrumentedTTS):
    """Control-only TTS guard: rate-limit / budget / fallback, no metering."""

    def __init__(self, control: GuardControl, **kwargs: Any) -> None:
        super().__init__(metering=False, **kwargs)
        self._control = control

    def synthesize(
        self, text: str, *, conn_options: Any = DEFAULT_API_CONNECT_OPTIONS
    ) -> Any:
        return _GuardTTSStream(
            control=self._control,
            method="synthesize",
            args=(text,),
            conn_options=conn_options,
        )

    def stream(self, *, conn_options: Any = DEFAULT_API_CONNECT_OPTIONS) -> Any:
        return _GuardTTSStream(
            control=self._control,
            method="stream",
            args=(),
            conn_options=conn_options,
        )


class _GuardTTSStream:
    """Lazy guard stream for TTS synthesize()/stream() (mirrors the LLM stream)."""

    def __init__(
        self,
        *,
        control: GuardControl,
        method: str,
        args: tuple[Any, ...],
        conn_options: Any,
    ) -> None:
        self._control = control
        self._method = method
        self._args = args
        self._conn_options = conn_options

    def __aiter__(self) -> Any:
        return self._agen()

    async def _agen(self) -> Any:
        await self._control.preflight()

        def _open(provider: Any) -> Any:
            method = getattr(provider, self._method)
            return method(*self._args, conn_options=self._conn_options)

        async for item in self._control.iterate_with_fallback(_open):
            yield item

    async def aclose(self) -> None:
        return None

    async def __aenter__(self) -> Any:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()


_GUARD_CLS = {
    "stt": GuardedSTT,
    "llm": GuardedLLM,
    "tts": GuardedTTS,
}


def _modality_of(provider: Any) -> str | None:
    """Return the provider's modality by its livekit.agents base class."""
    from livekit.agents import llm as lk_llm
    from livekit.agents import stt as lk_stt
    from livekit.agents import tts as lk_tts

    if isinstance(provider, lk_stt.STT):
        return "stt"
    if isinstance(provider, lk_llm.LLM):
        return "llm"
    if isinstance(provider, lk_tts.TTS):
        return "tts"
    return None


def guard_livekit(
    provider: Any,
    *,
    fallback: Any = (),
    rate_limit: str | None = None,
    budget: str | None = None,
    project: str | None = None,
    spend_reader: Any = None,
) -> Any:
    """Build a control-only LiveKit guard wrapper around ``provider``.

    Returns a ``livekit.agents.{stt,llm,tts}`` subclass wrapping ``provider``. The
    wrapper meters nothing (attach is the sole meter); it adds fallback,
    rate-limit, and budget control. ``spend_reader`` is an injectable
    ``(project, period) -> float | awaitable`` for budget lookups (defaults to
    the gateway storage); tests pass a fake to avoid a live DB.
    """
    modality = _modality_of(provider)
    if modality is None:
        raise ValueError(
            f"guard(): {type(provider).__name__!r} is not a recognized LiveKit "
            "STT/LLM/TTS provider"
        )

    control = GuardControl(
        provider,
        fallback=fallback,
        rate_limit=rate_limit,
        budget=budget,
        project=project,
        spend_reader=spend_reader,
    )
    provider_id, model_id = component_identity(provider)
    wrapper_cls = _GUARD_CLS[modality]
    return wrapper_cls(
        control,
        wrapped=provider,
        model_id=model_id,
        provider=provider_id,
        project=project or "default",
        cost_tracker=_noop_cost_tracker(),
        storage=None,
    )


__all__ = [
    "GuardControl",
    "GuardedLLM",
    "GuardedSTT",
    "GuardedTTS",
    "guard_livekit",
]
