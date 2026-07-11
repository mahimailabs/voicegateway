"""RateCard.with_overrides + rate_rule_from_row: merging DB overrides onto seed.

DB override rows are appended after the seed rules, so a DB rule wins a
specificity tie against a seed rule at the same scope (later-wins).
"""

from __future__ import annotations

import pytest

from voicegateway.billing.rate_card import RateCard, RateRule, rate_rule_from_row


def test_rate_rule_from_row_maps_fields() -> None:
    row = {
        "modality": "stt",
        "provider": "deepgram",
        "model": "nova-3",
        "tenant": "acme",
        "plan": None,
        "kind": "fixed",
        "markup": None,
        "unit_price_usd": 0.006,
        "unit": "minute",
    }
    rule = rate_rule_from_row(row)
    assert rule == RateRule(
        modality="stt",
        provider="deepgram",
        model="nova-3",
        tenant="acme",
        kind="fixed",
        unit_price_usd=0.006,
        unit="minute",
    )


def test_with_overrides_appends_after_seed() -> None:
    seed = RateCard(
        rules=[RateRule(provider="deepgram", markup=1.5)], default_markup=1.3
    )
    merged = seed.with_overrides(
        [{"provider": "deepgram", "kind": "cost_plus", "markup": 1.9}]
    )
    # Seed untouched; merged keeps default and has both rules.
    assert len(seed.rules) == 1
    assert merged.default_markup == 1.3
    assert len(merged.rules) == 2


def test_db_override_wins_specificity_tie() -> None:
    seed = RateCard(rules=[RateRule(provider="deepgram", markup=1.5)])
    merged = seed.with_overrides(
        [{"provider": "deepgram", "kind": "cost_plus", "markup": 1.9}]
    )
    hit = merged.resolve(
        modality="stt", provider="deepgram", model_id="deepgram/nova-3"
    )
    assert hit is not None
    assert hit.markup == 1.9  # DB override (appended last) wins the tie


def test_no_overrides_returns_equivalent_card() -> None:
    seed = RateCard(rules=[RateRule(provider="openai", markup=1.4)], default_markup=1.2)
    merged = seed.with_overrides([])
    assert merged.rules == seed.rules
    assert merged.default_markup == pytest.approx(1.2)
