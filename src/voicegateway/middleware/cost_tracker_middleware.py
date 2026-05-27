"""Per-request cost calculation and storage."""

from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from voicegateway.inference.pricing import catalog
from voicegateway.models.request_model import RequestRecord

if TYPE_CHECKING:
    from voicegateway.middleware.budget_enforcer_middleware import BudgetEnforcer

logger = logging.getLogger(__name__)


class CostTracker:
    """Tracks per-request costs based on provider pricing."""

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
        cached_input_units: float = 0.0,
    ) -> float:
        """Calculate cost for a request."""
        if modality == "stt":
            cost = catalog.calculate_cost(
                "stt", model_id, audio_seconds=input_units * 60
            )
        elif modality == "llm":
            cost = catalog.calculate_cost(
                "llm",
                model_id,
                input_tokens=int(input_units),
                output_tokens=int(output_units),
                cached_input_tokens=int(cached_input_units),
            )
        elif modality == "tts":
            cost = catalog.calculate_cost(
                "tts", model_id, character_count=int(input_units)
            )
        else:
            return 0.0

        if cost is None:
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
        cached_input_units: float = 0.0,
        ttfb_ms: float | None = None,
        total_latency_ms: float | None = None,
        status: str = "success",
        fallback_from: str | None = None,
        error_message: str | None = None,
        pricing_source: str = "",
        session_id: str | None = None,
    ) -> RequestRecord:
        """Create a request record with cost calculated."""
        cost = self.calculate_cost(
            model_id, modality, input_units, output_units, cached_input_units
        )
        if not pricing_source:
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
            cached_input_units=cached_input_units,
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
        """Log a request record to storage and update the budget cache."""
        try:
            if self._storage:
                await self._storage.log_request(record)
        finally:
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
        """Finalize session-aggregate metrics + replay on session close."""
        if self._storage is None:
            return
        metrics_finalize = getattr(self._storage, "finalize_session_metrics", None)
        if metrics_finalize is None:
            logger.debug(
                "CostTracker.close_session: storage has no "
                "finalize_session_metrics; skipping",
            )
        else:
            try:
                await metrics_finalize(session_id)
            except Exception:
                logger.warning(
                    "Failed to finalize metrics for session %s",
                    session_id,
                    exc_info=True,
                )

        replay_finalize = getattr(self._storage, "finalize_session_replay", None)
        if replay_finalize is None:
            logger.debug(
                "CostTracker.close_session: storage has no "
                "finalize_session_replay; skipping",
            )
            return
        try:
            await replay_finalize(session_id)
        except Exception:
            logger.warning(
                "Failed to finalize replay for session %s",
                session_id,
                exc_info=True,
            )
