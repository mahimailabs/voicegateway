"""FX conversion fails loudly or not at all.

The point of this module is that a converted price is auditable and never
silently stale, so most of these tests are about refusing rather than
converting. A fallback rate would defeat the whole feature: the price would
still be written, and nothing downstream could tell it was written from a guess.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from voicegateway.billing import fx

# ---------------------------------------------------------------------------
# parse_money
# ---------------------------------------------------------------------------


def test_parses_currency_and_amount():
    assert fx.parse_money("INR:3.00") == ("INR", Decimal("3.00"))


def test_currency_is_upper_cased_and_whitespace_tolerated():
    assert fx.parse_money(" inr : 3.00 ") == ("INR", Decimal("3.00"))


@pytest.mark.parametrize(
    "spec",
    ["INR", "INR:", ":3.00", "", "RUPEES:3.00", "IN:3.00", "INR:abc"],
)
def test_malformed_specs_are_refused(spec):
    """A typo must not become a price."""
    with pytest.raises(fx.FxError):
        fx.parse_money(spec)


@pytest.mark.parametrize("spec", ["INR:0", "INR:-3.00"])
def test_non_positive_amounts_are_refused(spec):
    with pytest.raises(fx.FxError):
        fx.parse_money(spec)


def test_amount_keeps_decimal_precision():
    """Not a float: 0.1 + 0.2 problems have no place in a price."""
    _, amount = fx.parse_money("INR:0.0000001")
    assert amount == Decimal("0.0000001")
    assert isinstance(amount, Decimal)


# ---------------------------------------------------------------------------
# fetch_rate: the failure contract
# ---------------------------------------------------------------------------


def _stub(monkeypatch, *, json_payload=None, exc=None, status=200):
    def fake_get(url, timeout=None):
        if exc is not None:
            raise exc
        request = httpx.Request("GET", url)
        return httpx.Response(status, json=json_payload, request=request)

    monkeypatch.setattr(fx.httpx, "get", fake_get)


def test_usd_short_circuits_without_a_network_call(monkeypatch):
    """--from USD:0.03 is a legal no-op, not a special case for the caller."""

    def explode(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("USD must not hit the network")

    monkeypatch.setattr(fx.httpx, "get", explode)
    quote = fx.fetch_rate("USD")
    assert quote.rate == Decimal(1)
    assert quote.source == "identity"


def test_happy_path_returns_a_decimal_rate(monkeypatch):
    _stub(monkeypatch, json_payload={"rates": {"INR": 95.513702}})
    quote = fx.fetch_rate("INR")
    assert quote.currency == "INR"
    assert quote.rate == Decimal("95.513702")
    assert quote.fetched_at > 0


def test_rate_is_parsed_via_str_not_float(monkeypatch):
    """Decimal(float) would inherit the binary error this module exists to avoid."""
    _stub(monkeypatch, json_payload={"rates": {"INR": 0.1}})
    assert fx.fetch_rate("INR").rate == Decimal("0.1")


def test_network_failure_raises_rather_than_falling_back(monkeypatch):
    _stub(monkeypatch, exc=httpx.ConnectError("no route to host"))
    with pytest.raises(fx.FxError, match="could not reach"):
        fx.fetch_rate("INR")


def test_http_error_raises(monkeypatch):
    _stub(monkeypatch, json_payload={}, status=503)
    with pytest.raises(fx.FxError):
        fx.fetch_rate("INR")


def test_missing_rates_object_raises(monkeypatch):
    _stub(monkeypatch, json_payload={"result": "success"})
    with pytest.raises(fx.FxError, match="no 'rates' object"):
        fx.fetch_rate("INR")


def test_unquoted_currency_raises(monkeypatch):
    _stub(monkeypatch, json_payload={"rates": {"EUR": 0.9}})
    with pytest.raises(fx.FxError, match="does not quote INR"):
        fx.fetch_rate("INR")


@pytest.mark.parametrize("bad", [0, -1, "abc"])
def test_nonsense_rates_raise(monkeypatch, bad):
    """A zero or negative rate would divide wrongly or blow up downstream."""
    _stub(monkeypatch, json_payload={"rates": {"INR": bad}})
    with pytest.raises(fx.FxError):
        fx.fetch_rate("INR")


# ---------------------------------------------------------------------------
# resolve_url
# ---------------------------------------------------------------------------


def test_default_endpoint_when_env_unset(monkeypatch):
    monkeypatch.delenv(fx.ENV_FX_URL, raising=False)
    assert fx.resolve_url() == fx.DEFAULT_FX_URL


def test_env_override_wins(monkeypatch):
    monkeypatch.setenv(fx.ENV_FX_URL, "https://fx.internal/latest")
    assert fx.resolve_url() == "https://fx.internal/latest"


def test_blank_env_falls_back_to_default(monkeypatch):
    """An exported-but-empty var is a deployment accident, not a choice."""
    monkeypatch.setenv(fx.ENV_FX_URL, "   ")
    assert fx.resolve_url() == fx.DEFAULT_FX_URL


# ---------------------------------------------------------------------------
# to_usd
# ---------------------------------------------------------------------------


def test_conversion_round_trips_exactly():
    """The published figure must be recoverable from what we stored."""
    quote = fx.FxQuote(
        currency="INR", rate=Decimal("95.513702"), source="t", fetched_at=0.0
    )
    usd = fx.to_usd(Decimal("3.00"), quote)
    assert (usd * quote.rate).quantize(Decimal("0.01")) == Decimal("3.00")


def test_conversion_keeps_more_precision_than_cents():
    """A per-character rate is far below a cent; rounding here would zero it."""
    quote = fx.FxQuote(
        currency="INR", rate=Decimal("95.513702"), source="t", fetched_at=0.0
    )
    usd = fx.to_usd(Decimal("3.00"), quote)
    assert usd > Decimal("0.0314")
    assert usd < Decimal("0.0315")


def test_usd_identity_conversion_is_the_amount():
    quote = fx.fetch_rate("USD")
    assert fx.to_usd(Decimal("0.03"), quote) == Decimal("0.03")
