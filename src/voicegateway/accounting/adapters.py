"""Compatibility mapping from existing SDK metering into usage v1."""

from __future__ import annotations

import time
import uuid
from decimal import Decimal

from voicegateway.accounting.contracts import (
    MeasurementStatus,
    OwnershipMode,
    PricingBinding,
    PricingDimension,
    Quantity,
    UsageEnvelope,
)
from voicegateway.models.request_model import RequestRecord


def _decimal(value: Decimal | float | int) -> str:
    return format(Decimal(str(value)), "f")


def envelope_from_request_record(
    record: RequestRecord,
    *,
    producer_id: str,
    binding: PricingBinding | None = None,
    event_id: str | None = None,
    attempt_id: str | None = None,
    component: str = "conversation",
) -> UsageEnvelope:
    """Map the measurements the legacy adapters actually expose.

    This creates a new provider-attempt identity unless the capture hook passes
    one explicitly. Delivery code must persist and retry the returned envelope,
    not call this function again for a retry.
    """
    measured: dict[PricingDimension, str] = {
        PricingDimension.REQUESTS: "1",
    }
    if record.modality == "llm":
        measured.update(
            {
                PricingDimension.TEXT_INPUT: _decimal(record.input_units),
                PricingDimension.TEXT_OUTPUT: _decimal(record.output_units),
                PricingDimension.CACHE_READ: _decimal(record.cached_input_units),
            }
        )
    elif record.modality == "stt":
        measured[PricingDimension.AUDIO_SECONDS] = _decimal(
            Decimal(str(record.input_units)) * 60
        )
    elif record.modality == "tts":
        measured[PricingDimension.CHARACTERS] = _decimal(record.input_units)

    quantities = tuple(
        Quantity(
            dimension=dimension,
            value=measured.get(dimension),
            status=(
                MeasurementStatus.MEASURED
                if dimension in measured
                else MeasurementStatus.UNSUPPORTED
            ),
        )
        for dimension in PricingDimension
    )
    ownership = binding.ownership_mode if binding is not None else OwnershipMode.SDK
    return UsageEnvelope(
        event_id=event_id or str(uuid.uuid4()),
        attempt_id=attempt_id or record.id,
        project_id=record.project,
        session_id=record.session_id or f"request:{record.id}",
        turn_id=str(record.turn_index) if record.turn_index is not None else None,
        component=component,
        modality=record.modality,
        offering=record.model_id,
        model_id=record.model_id,
        producer_id=producer_id,
        ownership_mode=ownership,
        pricing_binding_id=binding.binding_id if binding is not None else None,
        acquisition_revision_id=(
            binding.acquisition_revision_id if binding is not None else None
        ),
        selling_revision_id=(
            binding.selling_revision_id if binding is not None else None
        ),
        occurred_at_ns=int(Decimal(str(record.timestamp)) * 1_000_000_000)
        if record.timestamp
        else time.time_ns(),
        quantities=quantities,
    )
