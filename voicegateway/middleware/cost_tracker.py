"""Per-request cost calculation and storage."""

from __future__ import annotations

import time
import uuid
from typing import Any

from voicegateway.pricing.catalog import get_pricing
from voicegateway.storage.models import RequestRecord


class CostTracker:
    """Tracks per-request costs based on provider pricing.

    For STT: cost = audio_duration_minutes * price_per_minute
    For LLM: cost = (input_tokens * input_price + output_tokens * output_price) / 1000
    For TTS: cost = characters * price_per_character
    """

    def __init__(self, storage: Any = None):
        self._storage = storage

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
        """Log a request record to storage."""
        if self._storage:
            await self._storage.log_request(record)
