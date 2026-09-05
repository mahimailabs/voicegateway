"""Exact rating for immutable usage envelopes."""

from __future__ import annotations

from decimal import Decimal, localcontext

from voicegateway.accounting.contracts import (
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
    quantities = {item.dimension: item for item in envelope.quantities}
    total = Decimal(0)
    complete = True
    with localcontext() as ctx:
        ctx.prec = 60
        for dimension, quantity in quantities.items():
            if quantity.value is None:
                complete = False
                continue
            rate = rates.get(dimension)
            if rate is None:
                complete = False
                continue
            value = Decimal(quantity.value)
            if dimension is PricingDimension.TEXT_INPUT:
                value -= sum(
                    Decimal(quantities[d].value or "0")
                    for d in (PricingDimension.CACHE_READ, PricingDimension.CACHE_WRITE)
                    if d in quantities
                )
            total += value * rate
        return format(total.quantize(_QUANTUM), "f"), complete
