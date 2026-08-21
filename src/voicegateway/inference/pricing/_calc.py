"""One place that asks voice-prices for a price and reports what came back.

The three modality modules each split ``provider/model``, built a ``Usage``,
called ``calc_price`` and collapsed the answer to ``Decimal | None``. That
collapse threw away the distinction this module exists to keep.

A model can MATCH the catalogue and still carry no rate for the units
recorded. ``deepgram/nova-general`` matches the catalogue entry ``nova``,
whose ``ModelPrice`` has every field ``None``. Nothing raises, no rate is
applied, and the total is ``Decimal('0')``: a confident zero that is
indistinguishable, downstream, from a model that genuinely costs nothing.

voice-prices reports this as ``unpriced_usage``, a tuple naming the usage
fields it could not price. ``getattr`` with a default because the attribute
arrived in 0.6.0 and the floor pin still admits 0.3.0, where a rateless entry
raises ``LookupError`` instead and takes the honest path anyway.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from voice_prices import calc_price

if TYPE_CHECKING:
    from voice_prices import Usage


def split_ref(model: str) -> tuple[str, str]:
    """``(provider, ref)`` from a ``provider/model`` string.

    A bare model with no slash has no provider, and voice-prices is left to
    match on the ref alone.
    """
    if "/" in model:
        provider, _, ref = model.partition("/")
        return provider, ref
    return "", model


def price_usage(usage: Usage, model: str) -> tuple[Decimal | None, tuple[str, ...]]:
    """Return ``(total, unpriced_units)`` for ``usage`` against ``model``.

    ``total`` is ``None`` when the model is unknown to the catalogue.

    ``unpriced_units`` names the usage fields the catalogue matched but could
    not put a rate to. Non-empty means the total is not fully rate-backed, and
    the caller must not report it as a priced figure. It carries no claim
    about WHY: a deliberate free tier and a missing rate look identical here,
    because the catalogue does not distinguish them.
    """
    provider, ref = split_ref(model)
    try:
        price = calc_price(usage, model_ref=ref, provider_id=provider or None)
    except LookupError:
        return None, ()
    if price is None:
        return None, ()
    unpriced = tuple(getattr(price, "unpriced_usage", ()) or ())
    return Decimal(str(price.total_price)), unpriced


__all__ = ["price_usage", "split_ref"]
