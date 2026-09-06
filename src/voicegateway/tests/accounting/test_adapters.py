from __future__ import annotations

from voicegateway.accounting.adapters import envelope_from_request_record
from voicegateway.accounting.contracts import MeasurementStatus, PricingDimension
from voicegateway.models.request_model import RequestRecord


def _record(
    modality: str, *, input_units: float, output_units: float = 0
) -> RequestRecord:
    return RequestRecord(
        id=f"request-{modality}",
        timestamp=1.5,
        modality=modality,
        model_id="provider/model",
        provider="provider",
        input_units=input_units,
        output_units=output_units,
        cached_input_units=2,
        session_id="session-1",
    )


def test_llm_adapter_preserves_cache_subset_and_stable_attempt() -> None:
    envelope = envelope_from_request_record(
        _record("llm", input_units=10, output_units=3),
        producer_id="sdk-1",
        event_id="event-1",
    )
    values = {item.dimension: item.value for item in envelope.quantities}
    assert envelope.attempt_id == "request-llm"
    assert values[PricingDimension.TEXT_INPUT] == "10"
    assert values[PricingDimension.CACHE_READ] == "2"
    assert values[PricingDimension.TEXT_OUTPUT] == "3"


def test_stt_minutes_are_normalized_to_seconds() -> None:
    envelope = envelope_from_request_record(
        _record("stt", input_units=1.25), producer_id="sdk-1"
    )
    audio = next(
        item
        for item in envelope.quantities
        if item.dimension is PricingDimension.AUDIO_SECONDS
    )
    assert audio.value == "75"
    cache = next(
        item
        for item in envelope.quantities
        if item.dimension is PricingDimension.CACHE_READ
    )
    assert cache.status is MeasurementStatus.UNSUPPORTED
