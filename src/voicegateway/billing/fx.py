"""Fetch a foreign-exchange rate, once, at the moment an operator sets a price.

A vendor that publishes in INR or CNY cannot be priced from the catalog:
``voice-prices`` is USD-only and refuses to convert, because an FX rate moves
daily and a converted price would have no honest date on it. The same objection
holds inside VoiceGateway, harder: ``accounting.contracts`` types currency as
``Literal["USD"]``, and a stored total is meant to be immutable, so a rate that
floats would make a cost recorded in March read differently in September.

This module exists so an operator can convert **once**, deliberately, and store
the result as a fixed USD figure. The network call happens when someone runs
``voicegw prices set --from``, never on the metering or pricing path. Nothing
here is imported by a request.

Two properties are load-bearing:

- **No fallback.** Any failure raises :class:`FxError`. There is no cached rate,
  no last-known-good, no proceeding with a warning. A silent fallback is exactly
  how a stale number enters a price list and stops being visible as stale.
- **No rate is stored in this repository.** The quote is fetched live and
  written to the operator's database alongside the converted figure, so the
  provenance travels with the price rather than aging in source control.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import httpx

#: Key-free, ECB-backed, no signup. Overridable so an operator is not locked to
#: one vendor, and so a deployment with an internal FX service can point at it.
DEFAULT_FX_URL = "https://open.er-api.com/v6/latest/USD"

#: Environment override for :data:`DEFAULT_FX_URL`.
ENV_FX_URL = "VOICEGW_FX_URL"

_TIMEOUT_SECONDS = 10.0


class FxError(RuntimeError):
    """Raised when a rate cannot be fetched, parsed, or trusted.

    Deliberately not a subclass of anything the CLI catches by accident: a
    failed conversion must stop the command, not degrade it.
    """


@dataclass(frozen=True)
class FxQuote:
    """One fetched rate, with everything needed to audit it later."""

    #: ISO-4217-ish code as the operator typed it, upper-cased.
    currency: str
    #: Units of :attr:`currency` per 1 USD, e.g. ``95.513702`` for INR.
    rate: Decimal
    #: The URL the rate came from, stored so a later reader can re-check it.
    source: str
    #: Unix epoch at fetch time. The staleness clock starts here, not at the
    #: provider's publication time, because that is what we can attest to.
    fetched_at: float


def parse_money(spec: str) -> tuple[str, Decimal]:
    """Parse a ``CUR:AMOUNT`` flag value into ``(currency, amount)``.

    >>> parse_money("INR:3.00")
    ('INR', Decimal('3.00'))
    """
    currency, _, raw_amount = spec.partition(":")
    currency = currency.strip().upper()
    raw_amount = raw_amount.strip()
    if not currency or not raw_amount:
        raise FxError(f"expected CURRENCY:AMOUNT (e.g. INR:3.00), got {spec!r}")
    if not currency.isalpha() or len(currency) != 3:
        raise FxError(f"{currency!r} is not a 3-letter currency code")
    try:
        amount = Decimal(raw_amount)
    except InvalidOperation:
        raise FxError(f"{raw_amount!r} is not a number") from None
    if amount <= 0:
        raise FxError(f"amount must be positive, got {amount}")
    return currency, amount


def resolve_url() -> str:
    """The FX endpoint in effect, honouring the environment override."""
    return os.environ.get(ENV_FX_URL, "").strip() or DEFAULT_FX_URL


def fetch_rate(currency: str, *, url: str | None = None) -> FxQuote:
    """Fetch units-of-``currency`` per 1 USD. Raises :class:`FxError` on any doubt.

    USD is accepted and short-circuits to a rate of 1 without a network call,
    so ``--from USD:0.03`` is a legal no-op rather than a special case the
    caller has to remember.
    """
    currency = currency.upper()
    endpoint = url or resolve_url()
    if currency == "USD":
        return FxQuote(
            currency="USD",
            rate=Decimal(1),
            source="identity",
            fetched_at=time.time(),
        )
    try:
        response = httpx.get(endpoint, timeout=_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        raise FxError(f"could not reach the FX endpoint {endpoint}: {exc}") from None
    except ValueError as exc:  # json decode
        raise FxError(f"{endpoint} did not return JSON: {exc}") from None

    rates = payload.get("rates") if isinstance(payload, dict) else None
    if not isinstance(rates, dict):
        raise FxError(f"{endpoint} returned no 'rates' object")
    raw = rates.get(currency)
    if raw is None:
        raise FxError(f"{endpoint} does not quote {currency}")
    try:
        # str() first: the payload carries a float, and Decimal(float) would
        # inherit the binary representation error we are here to avoid.
        rate = Decimal(str(raw))
    except InvalidOperation:
        raise FxError(
            f"{endpoint} quoted {currency} as {raw!r}, not a number"
        ) from None
    if rate <= 0:
        raise FxError(f"{endpoint} quoted {currency} as {rate}, which cannot be a rate")
    return FxQuote(
        currency=currency,
        rate=rate,
        source=endpoint,
        fetched_at=time.time(),
    )


def to_usd(amount: Decimal, quote: FxQuote) -> Decimal:
    """Convert ``amount`` in ``quote.currency`` to USD.

    Returns full Decimal precision. The caller decides how to round, because
    the right quantum depends on the unit being priced (a per-character rate
    needs more places than a per-minute one).
    """
    if quote.rate <= 0:  # pragma: no cover - fetch_rate already refuses these
        raise FxError(f"cannot convert at a rate of {quote.rate}")
    return amount / quote.rate


__all__ = [
    "DEFAULT_FX_URL",
    "ENV_FX_URL",
    "FxError",
    "FxQuote",
    "fetch_rate",
    "parse_money",
    "resolve_url",
    "to_usd",
]
