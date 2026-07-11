"""Per-request cost calculation and storage."""

from __future__ import annotations

import logging
import time
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from voicegateway.billing import rating
from voicegateway.billing.rate_card import RateCard
from voicegateway.inference.pricing import catalog
from voicegateway.inference.session.context import current_tenant
from voicegateway.models.request_model import RequestRecord

if TYPE_CHECKING:
    from voicegateway.middleware.budget_enforcer_middleware import BudgetEnforcer

logger = logging.getLogger(__name__)

# When no rate card is wired in, rate every request as a cost pass-through
# (default_markup 1.0 -> rated == cost, rule "default:1").
_PASSTHROUGH_CARD = RateCard()


class CostTracker:
    """Tracks per-request costs based on provider pricing."""

    def __init__(self, storage: Any = None, rate_card: RateCard | None = None):
        self._storage = storage
        self._budget_enforcer: BudgetEnforcer | None = None
        self._rate_card = rate_card

    def set_budget_enforcer(self, enforcer: BudgetEnforcer | None) -> None:
        """Wire in a BudgetEnforcer so cost writes update its spend cache."""
        self._budget_enforcer = enforcer

    def set_rate_card(self, card: RateCard | None) -> None:
        """Wire in the rate card used to stamp billable prices at write time."""
        self._rate_card = card

    def _rate(
        self,
        model_id: str,
        modality: str,
        provider: str,
        cost_usd: float,
        input_units: float,
        output_units: float,
    ) -> rating.RatedResult:
        """Rate a request against the active card, resolving the current tenant.

        Rating never breaks a request write: any failure falls back to a
        cost pass-through so the row is still recorded with a billable price.
        """
        card = self._rate_card if self._rate_card is not None else _PASSTHROUGH_CARD
        try:
            return rating.price(
                card,
                modality=modality,
                provider=provider,
                model_id=model_id,
                cost_usd=cost_usd,
                input_units=input_units,
                output_units=output_units,
                tenant=current_tenant(),
            )
        except Exception:
            logger.warning(
                "Rating failed for %s; passing recorded cost through",
                model_id,
                exc_info=True,
            )
            return rating.RatedResult(rated_price_usd=cost_usd, rate_rule="default:1")

    def rate_record(self, record: RequestRecord) -> None:
        """Re-rate an existing record in place against the active card.

        The collector's ingest path uses this to rate fleet rows with its own
        rate card: agents record raw cost and rate at pass-through (no card
        client-side), so the collector is the source of truth for margins and
        must not trust an agent-supplied ``rated_price_usd``. The tenant is
        resolved from the context var (ingest sets it from the verified key),
        matching the ``tenant_id`` stamped at write time.
        """
        rated = self._rate(
            record.model_id,
            record.modality,
            record.provider,
            record.cost_usd,
            record.input_units,
            record.output_units,
        )
        record.rated_price_usd = rated.rated_price_usd
        record.rate_rule = rated.rate_rule

    def _catalog_cost(
        self,
        model_id: str,
        modality: str,
        input_units: float,
        output_units: float,
        cached_input_units: float,
    ) -> Decimal | None:
        """Map recorded units onto the catalog call for ``modality``."""
        if modality == "stt":
            return catalog.calculate_cost(
                "stt", model_id, audio_seconds=input_units * 60
            )
        if modality == "llm":
            return catalog.calculate_cost(
                "llm",
                model_id,
                input_tokens=int(input_units),
                output_tokens=int(output_units),
                cached_input_tokens=int(cached_input_units),
            )
        if modality == "tts":
            return catalog.calculate_cost(
                "tts", model_id, character_count=int(input_units)
            )
        return None

    def _resolve_cost(
        self,
        model_id: str,
        modality: str,
        input_units: float,
        output_units: float,
        cached_input_units: float,
    ) -> tuple[float, Decimal | None]:
        """Return ``(float_cost, raw_decimal_or_None)`` for a request.

        ``None`` means voice-prices did not recognize the model. A warning is
        logged for unpriced cloud models; self-hosted ``local/``/``ollama/``
        models are expected to be free and are not warned about.
        """
        cost = self._catalog_cost(
            model_id, modality, input_units, output_units, cached_input_units
        )
        if cost is None:
            if not model_id.startswith(("local/", "ollama/")):
                logger.warning(
                    "No pricing data for %s model %r; cost recorded as $0.",
                    modality,
                    model_id,
                )
            return 0.0, None
        return float(cost), cost

    def calculate_cost(
        self,
        model_id: str,
        modality: str,
        input_units: float = 0.0,
        output_units: float = 0.0,
        cached_input_units: float = 0.0,
    ) -> float:
        """Calculate cost for a request."""
        cost, _ = self._resolve_cost(
            model_id, modality, input_units, output_units, cached_input_units
        )
        return cost

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
        agent_id: str | None = None,
    ) -> RequestRecord:
        """Create a request record with cost calculated."""
        cost, cost_dec = self._resolve_cost(
            model_id, modality, input_units, output_units, cached_input_units
        )
        if not pricing_source:
            if model_id.startswith(("local/", "ollama/")):
                pricing_source = catalog.SELF_HOSTED_SOURCE
            elif cost_dec is not None:
                pricing_source = catalog.pricing_source(modality)
        rated = self._rate(
            model_id, modality, provider, cost, input_units, output_units
        )
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
            rated_price_usd=rated.rated_price_usd,
            rate_rule=rated.rate_rule,
            ttfb_ms=ttfb_ms,
            total_latency_ms=total_latency_ms,
            status=status,
            fallback_from=fallback_from,
            error_message=error_message,
            session_id=session_id,
            agent_id=agent_id,
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
