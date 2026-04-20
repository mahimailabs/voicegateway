"""Per-request cost calculation and storage."""

from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from voicegateway.pricing.catalog import get_pricing
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

        Args:
            model_id: Full model ID (e.g., "deepgram/nova-3").
            modality: "stt", "llm", or "tts".
            input_units: Minutes (STT), input tokens (LLM), characters (TTS).
            output_units: Output tokens (LLM only).

        Returns:
            Cost in USD.
        """
        pricing = get_pricing(model_id, modality)

        if modality == "stt":
            return input_units * pricing.get("per_minute", 0.0)
        elif modality == "llm":
            input_cost = input_units * pricing.get("input_per_1k", 0.0) / 1000
            output_cost = output_units * pricing.get("output_per_1k", 0.0) / 1000
            return input_cost + output_cost
        elif modality == "tts":
            return input_units * pricing.get("per_character", 0.0)
        return 0.0

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
    ) -> RequestRecord:
        """Create a request record with cost calculated."""
        cost = self.calculate_cost(model_id, modality, input_units, output_units)
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
            ttfb_ms=ttfb_ms,
            total_latency_ms=total_latency_ms,
            status=status,
            fallback_from=fallback_from,
            error_message=error_message,
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
