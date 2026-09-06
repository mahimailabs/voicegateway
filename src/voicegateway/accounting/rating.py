"""Exact rating for immutable usage envelopes."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from voicegateway.accounting.contracts import (
    MeasurementStatus,
    PricingDimension,
    PricingRevisionCreate,
    UsageEnvelope,
)

_QUANTUM = Decimal("0.000000000001")


def rate_usage(
    envelope: UsageEnvelope, revision: PricingRevisionCreate
) -> tuple[str | None, bool]:
    """Return the exact rounded total and whether every quantity was rateable."""
    rates = {item.dimension: Decimal(item.rate) for item in revision.rates}
    unsupported = set(revision.unsupported_dimensions)
    quantities = {item.dimension: item for item in envelope.quantities}
    total = Decimal(0)
    complete = True
    with localcontext() as ctx:
        ctx.prec = 60
        ctx.rounding = ROUND_HALF_EVEN
        for dimension in PricingDimension:
            quantity = quantities.get(dimension)
            rate = rates.get(dimension)
            if rate is None:
                if quantity is not None and not (
                    quantity.status is MeasurementStatus.UNSUPPORTED
                    and dimension in unsupported
                ):
                    complete = False
                continue
            if quantity is None or quantity.value is None:
                complete = False
                continue
            value = Decimal(quantity.value)
            if dimension is PricingDimension.TEXT_INPUT:
                value -= sum(
                    Decimal(quantities[d].value or "0")
                    for d in (PricingDimension.CACHE_READ, PricingDimension.CACHE_WRITE)
                    if d in quantities and d in rates
                )
            total += value * rate
        return format(total.quantize(_QUANTUM), "f"), complete
