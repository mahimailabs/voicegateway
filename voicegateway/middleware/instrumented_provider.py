"""Transparent instrumentation wrappers for provider instances.

Wraps STT, LLM, and TTS instances returned by providers to automatically
record latency and cost metrics. All attribute access is proxied to the
underlying instance so user code sees no API changes.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from voicegateway.middleware.cost_tracker import CostTracker
    from voicegateway.storage.sqlite import SQLiteStorage

logger = logging.getLogger(__name__)


class _InstrumentedBase:
    """Base wrapper that proxies attribute access and logs a request record."""

    _modality: str = ""

    def __init__(
        self,
        wrapped: Any,
        model_id: str,
        provider: str,
        project: str,
        cost_tracker: CostTracker,
        storage: SQLiteStorage | None,
    ):
        # Use object.__setattr__ to avoid triggering __setattr__ proxy
        object.__setattr__(self, "_wrapped", wrapped)
        object.__setattr__(self, "_model_id", model_id)
        object.__setattr__(self, "_provider", provider)
        object.__setattr__(self, "_project", project)
        object.__setattr__(self, "_cost_tracker", cost_tracker)
        object.__setattr__(self, "_storage", storage)
        object.__setattr__(self, "_start_time", time.perf_counter())
        object.__setattr__(self, "_first_byte_time", None)
        object.__setattr__(self, "_logged", False)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_wrapped"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(object.__getattribute__(self, "_wrapped"), name, value)

    def __repr__(self) -> str:
        wrapped = object.__getattribute__(self, "_wrapped")
        return f"<Instrumented{self._modality.upper()} wrapping {wrapped!r}>"

    def _mark_first_byte(self) -> None:
        """Record the time of the first byte/token/result."""
        if object.__getattribute__(self, "_first_byte_time") is None:
            object.__setattr__(self, "_first_byte_time", time.perf_counter())

    async def _log_request(
        self,
        input_units: float = 0.0,
        output_units: float = 0.0,
        status: str = "success",
        error_message: str | None = None,
    ) -> None:
        """Log the request to storage."""
        if object.__getattribute__(self, "_logged"):
            return
        object.__setattr__(self, "_logged", True)

        start = object.__getattribute__(self, "_start_time")
        first_byte = object.__getattribute__(self, "_first_byte_time")
        model_id = object.__getattribute__(self, "_model_id")
        provider = object.__getattribute__(self, "_provider")
        project = object.__getattribute__(self, "_project")
        cost_tracker = object.__getattribute__(self, "_cost_tracker")
        storage = object.__getattribute__(self, "_storage")

        now = time.perf_counter()
        total_ms = (now - start) * 1000
        ttfb_ms = (first_byte - start) * 1000 if first_byte else total_ms

        record = cost_tracker.create_record(
            model_id=model_id,
            modality=self._modality,
            provider=provider,
            project=project,
            input_units=input_units,
            output_units=output_units,
            ttfb_ms=ttfb_ms,
            total_latency_ms=total_ms,
            status=status,
            error_message=error_message,
        )

        if storage is not None:
            try:
                await storage.log_request(record)
            except Exception:
                logger.warning("Failed to log request record", exc_info=True)

        # Update the budget enforcer's spend cache so the next check within
        # the TTL window sees this request's cost. Done regardless of
        # whether storage is enabled — the enforcer's cache is in-memory.
        await cost_tracker.notify_spend(record)


class InstrumentedSTT(_InstrumentedBase):
    """Wrapper for STT instances that records latency and cost."""

    _modality = "stt"


class InstrumentedLLM(_InstrumentedBase):
    """Wrapper for LLM instances that records latency and cost."""

    _modality = "llm"


class InstrumentedTTS(_InstrumentedBase):
    """Wrapper for TTS instances that records latency and cost."""

    _modality = "tts"


def wrap_provider(
    instance: Any,
    modality: str,
    model_id: str,
    provider: str,
    project: str,
    cost_tracker: CostTracker,
    storage: SQLiteStorage | None,
) -> Any:
    """Wrap a provider instance with instrumentation.

    Args:
        instance: The raw provider instance (STT, LLM, or TTS).
        modality: One of "stt", "llm", "tts".
        model_id: Full model ID string (e.g., "deepgram/nova-3").
        provider: Provider name (e.g., "deepgram").
        project: Project ID for cost tracking.
        cost_tracker: CostTracker instance for cost calculation.
        storage: SQLiteStorage instance (or None if disabled).

    Returns:
        Instrumented wrapper that proxies all access to the original instance.
    """
    wrapper_cls = {
        "stt": InstrumentedSTT,
        "llm": InstrumentedLLM,
        "tts": InstrumentedTTS,
    }.get(modality)

    if wrapper_cls is None:
        return instance

    return wrapper_cls(
        wrapped=instance,
        model_id=model_id,
        provider=provider,
        project=project,
        cost_tracker=cost_tracker,
        storage=storage,
    )
