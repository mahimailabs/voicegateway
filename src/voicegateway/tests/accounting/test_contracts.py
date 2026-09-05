from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from voicegateway.accounting.contracts import (
    DimensionRate,
    MeasurementStatus,
    OwnershipMode,
    PricingDimension,
    PricingRevisionCreate,
    PricingSide,
    Quantity,
    RevisionScope,
    Unit,
    UsageEnvelope,
)
from voicegateway.accounting.rating import rate_usage


def revision(side: PricingSide = PricingSide.SELLING) -> PricingRevisionCreate:
    rated = {
        PricingDimension.TEXT_INPUT: "0.000001",
        PricingDimension.TEXT_OUTPUT: "0.000002",
        PricingDimension.CACHE_READ: "0.0000001",
        PricingDimension.CACHE_WRITE: "0.0000002",
    }
    return PricingRevisionCreate(
        revision_id=f"{side}-v1",
        side=side,
        scope=RevisionScope(offering="provider/model"),
        rates=tuple(
            DimensionRate(dimension=d, unit=Unit.TOKEN, rate=value)
            for d, value in rated.items()
        ),
        unsupported_dimensions=tuple(set(PricingDimension) - set(rated)),
    )


def usage(**overrides: object) -> UsageEnvelope:
    values = {
        "event_id": "evt-1",
        "attempt_id": "attempt-1",
        "project_id": "default",
        "session_id": "session-1",
        "component": "conversation",
        "modality": "llm",
        "offering": "provider/model",
        "model_id": "provider/model",
        "producer_id": "sdk-1",
        "ownership_mode": OwnershipMode.SDK,
        "selling_revision_id": "selling-v1",
        "occurred_at_ns": 1,
        "quantities": (
            Quantity(
                dimension=PricingDimension.TEXT_INPUT,
                value="100",
                status=MeasurementStatus.MEASURED,
            ),
            Quantity(
                dimension=PricingDimension.CACHE_READ,
                value="20",
                status=MeasurementStatus.MEASURED,
            ),
            Quantity(
                dimension=PricingDimension.TEXT_OUTPUT,
                value="10",
                status=MeasurementStatus.MEASURED,
            ),
        ),
    }
    values.update(overrides)
    return UsageEnvelope.model_validate(values)


def test_revision_hash_is_canonical_and_models_are_frozen() -> None:
    first = revision()
    second = PricingRevisionCreate.model_validate(first.model_dump())
    assert first.content_hash() == second.content_hash()
    with pytest.raises(ValidationError):
        first.currency = "EUR"  # type: ignore[misc]


def test_revision_requires_complete_dimension_manifest() -> None:
    with pytest.raises(ValidationError, match="classify every"):
        PricingRevisionCreate(
            revision_id="bad",
            side="selling",
            scope={"offering": "provider/model"},
            rates=(),
        )


def test_decimal_rating_subtracts_cache_once() -> None:
    total, complete = rate_usage(usage(), revision())
    expected = (
        Decimal(80) * Decimal(".000001")
        + Decimal(20) * Decimal(".0000001")
        + Decimal(10) * Decimal(".000002")
    )
    assert Decimal(total or "0") == expected
    assert complete


def test_cache_cannot_exceed_input() -> None:
    with pytest.raises(ValidationError, match="subsets"):
        usage(
            quantities=(
                Quantity(dimension="text_input", value="1", status="measured"),
                Quantity(dimension="cache_read", value="2", status="measured"),
            )
        )


def test_missing_is_distinct_from_zero() -> None:
    with pytest.raises(ValidationError):
        Quantity(dimension="requests", value="0", status="missing")
    assert Quantity(dimension="requests", value="0", status="measured").value == "0"


@pytest.mark.parametrize(
    ("dimension", "unit", "quantity"),
    [
        (PricingDimension.TEXT_INPUT, Unit.TOKEN, "1"),
        (PricingDimension.TEXT_OUTPUT, Unit.TOKEN, "1"),
        (PricingDimension.CACHE_READ, Unit.TOKEN, "1"),
        (PricingDimension.CACHE_WRITE, Unit.TOKEN, "1"),
        (PricingDimension.REALTIME_AUDIO_INPUT, Unit.TOKEN, "1"),
        (PricingDimension.REALTIME_AUDIO_OUTPUT, Unit.TOKEN, "1"),
        (PricingDimension.REALTIME_AUDIO_CACHE, Unit.TOKEN, "1"),
        (PricingDimension.CHARACTERS, Unit.CHARACTER, "1"),
        (PricingDimension.AUDIO_SECONDS, Unit.SECOND, "1.000000001"),
        (PricingDimension.REQUESTS, Unit.REQUEST, "1"),
    ],
)
def test_all_dimensions_have_exact_fixed_units(dimension, unit, quantity) -> None:
    rate = DimensionRate(dimension=dimension, unit=unit, rate="0.000000000001")
    rates = [rate]
    quantities = [Quantity(dimension=dimension, value=quantity, status="measured")]
    if dimension in {PricingDimension.CACHE_READ, PricingDimension.CACHE_WRITE}:
        rates.append(
            DimensionRate(
                dimension=PricingDimension.TEXT_INPUT, unit=Unit.TOKEN, rate="0"
            )
        )
        quantities.append(
            Quantity(
                dimension=PricingDimension.TEXT_INPUT, value=quantity, status="measured"
            )
        )
    envelope = usage(quantities=tuple(quantities))
    price = PricingRevisionCreate(
        revision_id="dimension-v1",
        side="selling",
        scope={"offering": "provider/model"},
        rates=tuple(rates),
        unsupported_dimensions=tuple(
            set(PricingDimension) - {item.dimension for item in rates}
        ),
    )
    total, complete = rate_usage(envelope, price)
    assert Decimal(total or "0") >= 0
    assert complete


def test_non_audio_quantities_must_be_integer() -> None:
    with pytest.raises(ValidationError, match="integers"):
        Quantity(dimension="requests", value="0.5", status="measured")
