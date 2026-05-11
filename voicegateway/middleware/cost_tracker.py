"""Per-request cost calculation and storage."""

from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from voicegateway.pricing import catalog
from voicegateway.storage.models import RequestRecord

if TYPE_CHECKING:
    from voicegateway.middleware.budget_enforcer import BudgetEnforcer

logger = logging.getLogger(__name__)


class CostTracker:
    """Tracks per-request costs based on provider pricing.

    For STT: cost = audio_duration_minutes * price_per_minute
    For LLM: cost = (input_tokens * input_price + output_tokens * output_price) / 1000
    For TTS: cost = characters * price_per_character
    """

    def __init__(self, storage: Any = None):
        self._storage = storage
        self._budget_enforcer: BudgetEnforcer | None = None

    def set_budget_enforcer(self, enforcer: BudgetEnforcer | None) -> None:
        """Wire in a BudgetEnforcer so cost writes update its spend cache."""
        self._budget_enforcer = enforcer

    def calculate_cost(
        self,
        model_id: str,
        modality: str,
        input_units: float = 0.0,
        output_units: float = 0.0,
    ) -> float:
        """Calculate cost for a request.

        Dispatches through `voicegateway.pricing.catalog.calculate_cost`.
        For LLM, that wraps `pydantic/genai-prices`; for STT and TTS,
        the local source-date-tagged catalogs.

        Args:
            model_id: Full model ID (e.g., "deepgram/nova-3").
            modality: "stt", "llm", or "tts".
            input_units: Minutes (STT), input tokens (LLM), characters (TTS).
            output_units: Output tokens (LLM only).

        Returns:
            Cost in USD as float. The catalog returns Decimal | None;
            this method coerces Decimal to float and translates None
            into 0.0 with a warning log (except for known free
            providers like local/* and ollama/* where 0.0 is the
            correct answer and no warning fires).
        """
        if modality == "stt":
            # The legacy CostTracker contract for STT is `input_units`
            # in MINUTES. The new STT module uses audio_seconds. Convert
            # at the boundary to keep the legacy callers' semantics.
            cost = catalog.calculate_cost(
                "stt", model_id, audio_seconds=input_units * 60
            )
        elif modality == "llm":
            cost = catalog.calculate_cost(
                "llm",
                model_id,
                input_tokens=int(input_units),
                output_tokens=int(output_units),
            )
        elif modality == "tts":
            cost = catalog.calculate_cost(
                "tts", model_id, character_count=int(input_units)
            )
        else:
            return 0.0

        if cost is None:
            # local/* and ollama/* are intentionally free; no catalog
            # entry is expected and no warning is appropriate.
            if not model_id.startswith(("local/", "ollama/")):
                logger.warning(
                    "No pricing data for %s model %r; cost recorded as $0.",
                    modality,
                    model_id,
                )
            return 0.0
        return float(cost)

    def create_record(
        self,
        model_id: str,
        modality: str,
        provider: str,
        project: str = "default",
        input_units: float = 0.0,
        output_units: float = 0.0,
        ttfb_ms: float | None = None,
        total_latency_ms: float | None = None,
        status: str = "success",
        fallback_from: str | None = None,
        error_message: str | None = None,
        pricing_source: str = "",
        session_id: str | None = None,
    ) -> RequestRecord:
        """Create a request record with cost calculated.

        ``pricing_source`` defaults to the catalog facade's per-modality
        attribution string when not explicitly provided, so every record
        produced through the InstrumentedSTT/LLM/TTS wrappers carries
        the source automatically.
        """
        cost = self.calculate_cost(model_id, modality, input_units, output_units)
        if not pricing_source:
            # Only attribute to the catalog when it actually priced the
            # request. Unknown models that fell through to $0 must not
            # claim a catalog produced their number; that would mislead
            # /v1/costs?include_pricing_source and `voicegw reconcile`.
            # `local/*` and `ollama/*` are intentionally free, so the
            # catalog *did* price them as $0 and the source is honest.
            is_known_free = model_id.startswith(("local/", "ollama/"))
            if cost > 0.0 or is_known_free:
                pricing_source = catalog.pricing_source(modality)
        return RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=time.time(),
            project=project,
            modality=modality,
            model_id=model_id,
            provider=provider,
            input_units=input_units,
            output_units=output_units,
            cost_usd=cost,
            pricing_source=pricing_source,
            ttfb_ms=ttfb_ms,
            total_latency_ms=total_latency_ms,
            status=status,
            fallback_from=fallback_from,
            error_message=error_message,
            session_id=session_id,
        )

    async def log_request(self, record: RequestRecord) -> None:
        """Log a request record to storage and update the budget cache.

        The budget cache update runs in a ``finally`` so a transient
        storage failure doesn't leave the enforcer out of sync with the
        cost we just incurred — the request happened, the cost is real,
        and skipping the notify would silently undercount.
        """
        try:
            if self._storage:
                await self._storage.log_request(record)
        finally:
            # ``logged_at`` is captured after the write attempt so the
            # enforcer can skip its optimistic increment when a concurrent
            # cache refresh has already observed this row.
            logged_at = time.monotonic()
            await self.notify_spend(record, logged_at=logged_at)

    async def notify_spend(
        self, record: RequestRecord, logged_at: float | None = None
    ) -> None:
        """Notify the budget enforcer of a newly logged request."""
        if self._budget_enforcer is None or not record.cost_usd:
            return
        if logged_at is None:
            logged_at = time.monotonic()
        try:
            await self._budget_enforcer.record_spend(
                record.project, record.cost_usd, logged_at=logged_at
            )
        except Exception:
            logger.warning("Failed to update budget cache", exc_info=True)

    async def close_session(self, session_id: str) -> None:
        """Finalize session-aggregate metrics on session close.

        Delegates to :meth:`SQLiteStorage.finalize_session_metrics` so
        the v0.2.0 aggregate columns (``talk_time_seconds``,
        ``per_minute_cost_usd``, ``response_speed_p50/p95_ms``,
        ``talk_over_rate``) reflect the captured turn data by the time
        the ``/v1/metrics`` endpoint reads the sessions row.

        The actual TurnTracker flush and DeadAirDetector cancellation
        are owned by :func:`voicegateway.inference.attach_session` (T09);
        this method is the cost-tracking side of the close hook
        (Foundry item #7).

        No-ops when storage is missing (tests sometimes pass
        ``storage=None``) or the storage does not implement the
        ``finalize_session_metrics`` contract (older versions before
        T07's modification). Errors during finalization are logged but
        do not propagate: dropping an aggregate row should not crash
        the session-close path.
        """
        if self._storage is None:
            return
        finalize = getattr(self._storage, "finalize_session_metrics", None)
        if finalize is None:
            logger.debug(
                "CostTracker.close_session: storage has no "
                "finalize_session_metrics; skipping",
            )
            return
        try:
            await finalize(session_id)
        except Exception:
            logger.warning(
                "Failed to finalize metrics for session %s",
                session_id,
                exc_info=True,
            )
