"""Unit tests for the rating math: turning a resolved rule + a request's
recorded cost/units into a billable ``rated_price_usd`` plus an auditable
``rate_rule`` string.

Two rule kinds:

* ``cost_plus`` multiplies the recorded provider cost by a markup, so it
  auto-follows any voice-prices base movement.
* ``fixed`` multiplies an advertised ``$/unit`` by the request's billable
  quantity, decoupled from the base cost.
"""

from __future__ import annotations

import pytest

from voicegateway.billing.rate_card import RateCard, RateRule
from voicegateway.billing.rating import apply_rule, billable_quantity, price

# ----- billable_quantity ------------------------------------------------


@pytest.mark.parametrize(
    ("unit", "modality", "in_units", "out_units", "expected"),
    [
        ("minute", "stt", 2.0, 0.0, 2.0),
        ("second", "stt", 2.0, 0.0, 120.0),
        ("char", "tts", 500.0, 0.0, 500.0),
        ("1k_char", "tts", 500.0, 0.0, 0.5),
        ("token", "llm", 1000.0, 200.0, 1200.0),
        ("1m_token", "llm", 500000.0, 500000.0, 1.0),
        ("request", "llm", 1234.0, 99.0, 1.0),
    ],
)
def test_billable_quantity(unit, modality, in_units, out_units, expected) -> None:
    got = billable_quantity(
        unit, modality=modality, input_units=in_units, output_units=out_units
    )
    assert got == pytest.approx(expected)


def test_billable_quantity_rejects_unknown_unit() -> None:
    with pytest.raises(ValueError):
        billable_quantity("furlong", modality="stt", input_units=1.0, output_units=0.0)


# ----- apply_rule: cost_plus -------------------------------------------


def test_apply_cost_plus_multiplies_cost() -> None:
    rule = RateRule(kind="cost_plus", markup=1.3)
    result = apply_rule(
        rule, cost_usd=0.10, modality="llm", input_units=1000, output_units=100
    )
    assert result.rated_price_usd == pytest.approx(0.13)
    assert result.rate_rule == "cost_plus:1.3"


# ----- apply_rule: fixed ------------------------------------------------


def test_apply_fixed_prices_by_billable_quantity() -> None:
    rule = RateRule(kind="fixed", unit_price_usd=0.0060, unit="minute")
    result = apply_rule(
        rule, cost_usd=0.0043, modality="stt", input_units=2.0, output_units=0.0
    )
    assert result.rated_price_usd == pytest.approx(0.012)  # 2 min x $0.0060
    assert result.rate_rule == "fixed:0.006/minute"


# ----- price: end-to-end resolve + rate --------------------------------


def test_price_uses_matched_rule() -> None:
    card = RateCard(rules=[RateRule(provider="deepgram", markup=1.5)])
    result = price(
        card,
        modality="stt",
        provider="deepgram",
        model_id="deepgram/nova-3",
        cost_usd=0.0043,
        input_units=2.0,
    )
    assert result.rated_price_usd == pytest.approx(0.00645)
    assert result.rate_rule == "cost_plus:1.5"


def test_price_falls_back_to_default_markup_when_no_rule() -> None:
    card = RateCard(rules=[], default_markup=1.3)
    result = price(
        card,
        modality="llm",
        provider="openai",
        model_id="openai/gpt-4o",
        cost_usd=0.20,
        input_units=1000,
        output_units=100,
    )
    assert result.rated_price_usd == pytest.approx(0.26)
    assert result.rate_rule == "default:1.3"


def test_price_default_markup_one_is_passthrough() -> None:
    card = RateCard(rules=[])  # default_markup defaults to 1.0
    result = price(
        card,
        modality="tts",
        provider="cartesia",
        model_id="cartesia/sonic",
        cost_usd=0.05,
        input_units=500,
    )
    assert result.rated_price_usd == pytest.approx(0.05)
    assert result.rate_rule == "default:1"


def test_price_tenant_override_applied() -> None:
    card = RateCard(
        rules=[
            RateRule(model="nova-3", markup=2.0),
            RateRule(tenant="acme", markup=1.1),
        ]
    )
    result = price(
        card,
        modality="stt",
        provider="deepgram",
        model_id="deepgram/nova-3",
        cost_usd=0.0043,
        input_units=2.0,
        tenant="acme",
    )
    assert result.rated_price_usd == pytest.approx(0.00473)
    assert result.rate_rule == "cost_plus:1.1"
