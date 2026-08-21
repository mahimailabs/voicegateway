"""Per-request cost calculation and storage."""

from __future__ import annotations

import logging
import time
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Any, NamedTuple

from voicegateway.billing import rating
from voicegateway.billing.rate_card import RateCard
from voicegateway.inference.pricing import catalog
from voicegateway.inference.session.context import current_tenant
from voicegateway.models.request_model import RequestRecord

if TYPE_CHECKING:
    from voicegateway.middleware.budget_enforcer_middleware import BudgetEnforcer

logger = logging.getLogger(__name__)


class ResolvedCost(NamedTuple):
    """What a request cost, and which authority produced the number.

    ``source`` is set only when an operator-declared rate produced the cost,
    and becomes the row's ``pricing_source``. ``raw`` is None when nothing
    could price the request at all. ``unrated`` names units the catalogue
    matched and had no rate for, which is a zero that is not a price.
    """

    cost: float
    raw: Decimal | None
    unrated: tuple[str, ...]
    source: str | None


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
        cached_input_units: float = 0.0,
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
                cached_input_units=cached_input_units,
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
        # The collector is the source of truth for what things cost: agents
        # record the catalogue figure because they carry no card. If this
        # collector declares a cost for the model, the ingested row is
        # corrected to it before the markup is applied, so a margin is never
        # computed against a list price the operator does not pay.
        card = self._rate_card
        if card is not None and not record.model_id.startswith(("local/", "ollama/")):
            declared = rating.declared_cost(
                card,
                modality=record.modality,
                provider=record.provider,
                model_id=record.model_id,
                input_units=record.input_units,
                output_units=record.output_units,
                cached_input_units=record.cached_input_units,
                tenant=current_tenant(),
            )
            if declared is not None:
                total, rule = declared
                record.cost_usd = total
                record.pricing_source = f"rate-card:{rule.audit_token()}"

        rated = self._rate(
            record.model_id,
            record.modality,
            record.provider,
            record.cost_usd,
            record.input_units,
            record.output_units,
            record.cached_input_units,
        )
        record.rated_price_usd = rated.rated_price_usd
        record.rate_rule = rated.rate_rule

    @staticmethod
    def _provider_of(model_id: str) -> str:
        """Provider half of a ``provider/model`` id, for rule matching.

        ``create_record`` takes the provider as its own argument, but
        ``_resolve_cost`` is also reached from ``calculate_cost``, which does
        not. Deriving it keeps both paths matching the same rules rather than
        one of them silently missing every provider-scoped rule.
        """
        return model_id.split("/", 1)[0] if "/" in model_id else ""

    def _catalog_cost(
        self,
        model_id: str,
        modality: str,
        input_units: float,
        output_units: float,
        cached_input_units: float,
    ) -> tuple[Decimal | None, tuple[str, ...]]:
        """Map recorded units onto the catalog call for ``modality``.

        Returns the catalog's ``(total, unrated_units)`` pair: a non-empty
        second element means the model matched but no rate was applied.
        """
        if modality == "stt":
            return catalog.calculate_cost_detail(
                "stt", model_id, audio_seconds=input_units * 60
            )
        if modality == "llm":
            return catalog.calculate_cost_detail(
                "llm",
                model_id,
                input_tokens=int(input_units),
                output_tokens=int(output_units),
                cached_input_tokens=int(cached_input_units),
            )
        if modality == "tts":
            return catalog.calculate_cost_detail(
                "tts", model_id, character_count=int(input_units)
            )
        return None, ()

    def _resolve_cost(
        self,
        model_id: str,
        modality: str,
        input_units: float,
        output_units: float,
        cached_input_units: float,
    ) -> ResolvedCost:
        """Return the request's cost, and where the number came from.

        ``None`` means voice-prices did not recognize the model. A non-empty
        ``unrated_units`` means it DID recognize the model and applied no rate
        to those units, so the total is a zero the catalogue never priced.
        Both are warned about, in different words, because the remedies
        differ: an unknown model needs a catalogue entry, a rateless match
        needs a rate on an entry that already exists.

        Self-hosted ``local/``/``ollama/`` models are expected to be free and
        are not warned about.

        AN OPERATOR-DECLARED COST WINS OVER THE CATALOGUE, and is checked
        first. The catalogue holds published list prices; anyone at volume is
        on a negotiated contract that differs from them by a margin nobody
        outside the contract can see. When a cost rule matches, the row records
        what the operator actually pays and names the rule that said so, so
        ``reconcile`` can point at the entry rather than at the usage when the
        invoice disagrees.
        """
        card = self._rate_card
        if card is not None and not model_id.startswith(("local/", "ollama/")):
            declared = rating.declared_cost(
                card,
                modality=modality,
                provider=self._provider_of(model_id),
                model_id=model_id,
                input_units=input_units,
                output_units=output_units,
                cached_input_units=cached_input_units,
                tenant=current_tenant(),
            )
            if declared is not None:
                total, rule = declared
                return ResolvedCost(
                    cost=total,
                    raw=Decimal(str(total)),
                    unrated=(),
                    source=f"rate-card:{rule.audit_token()}",
                )

        cost, unrated = self._catalog_cost(
            model_id, modality, input_units, output_units, cached_input_units
        )
        if cost is None:
            if not model_id.startswith(("local/", "ollama/")):
                logger.warning(
                    "No pricing data for %s model %r; cost recorded as $0.",
                    modality,
                    model_id,
                )
            return ResolvedCost(cost=0.0, raw=None, unrated=(), source=None)
        if unrated:
            logger.warning(
                "%s model %r matched the catalog but it carries no rate for "
                "%s; cost recorded as $%s and tagged %r rather than as priced.",
                modality,
                model_id,
                ", ".join(unrated),
                cost,
                catalog.UNRATED_SOURCE,
            )
        return ResolvedCost(cost=float(cost), raw=cost, unrated=unrated, source=None)

    def calculate_cost(
        self,
        model_id: str,
        modality: str,
        input_units: float = 0.0,
        output_units: float = 0.0,
        cached_input_units: float = 0.0,
    ) -> float:
        """Calculate cost for a request."""
        return self._resolve_cost(
            model_id, modality, input_units, output_units, cached_input_units
        ).cost

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
        revision: str | None = None,
    ) -> RequestRecord:
        """Create a request record with cost calculated."""
        resolved = self._resolve_cost(
            model_id, modality, input_units, output_units, cached_input_units
        )
        cost = resolved.cost
        if not pricing_source:
            if resolved.source is not None:
                pricing_source = resolved.source
            elif model_id.startswith(("local/", "ollama/")):
                pricing_source = catalog.SELF_HOSTED_SOURCE
            elif resolved.unrated:
                pricing_source = catalog.UNRATED_SOURCE
            elif resolved.raw is not None:
                pricing_source = catalog.pricing_source(modality)
        rated = self._rate(
            model_id,
            modality,
            provider,
            cost,
            input_units,
            output_units,
            cached_input_units,
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
            revision=revision,
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
