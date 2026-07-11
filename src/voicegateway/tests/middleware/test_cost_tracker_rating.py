"""Write-time rating: CostTracker.create_record stamps rated_price_usd +
rate_rule onto each record using the injected rate card.

Rating is immutable at write time: the price the rate card yields when the
record is created is frozen onto the row, so later card edits never
retroactively change historical revenue.
"""

from __future__ import annotations

import pytest

from voicegateway.billing.rate_card import RateCard, RateRule
from voicegateway.inference.session.context import reset_tenant_id, set_tenant
from voicegateway.middleware.cost_tracker_middleware import CostTracker


def test_no_card_passes_recorded_cost_through() -> None:
    """With no rate card, rated price equals recorded cost (default:1)."""
    tracker = CostTracker()
    record = tracker.create_record(
        model_id="deepgram/nova-3",
        modality="stt",
        provider="deepgram",
        input_units=1.0,
    )
    assert record.cost_usd == pytest.approx(0.0048)
    assert record.rated_price_usd == pytest.approx(0.0048)
    assert record.rate_rule == "default:1"


def test_default_markup_card_applies_to_unmatched_models() -> None:
    tracker = CostTracker(rate_card=RateCard(default_markup=1.3))
    record = tracker.create_record(
        model_id="deepgram/nova-3",
        modality="stt",
        provider="deepgram",
        input_units=1.0,
    )
    assert record.rated_price_usd == pytest.approx(0.0048 * 1.3)
    assert record.rate_rule == "default:1.3"


def test_cost_plus_rule_marks_up_recorded_cost() -> None:
    tracker = CostTracker()
    tracker.set_rate_card(RateCard(rules=[RateRule(provider="deepgram", markup=1.5)]))
    record = tracker.create_record(
        model_id="deepgram/nova-3",
        modality="stt",
        provider="deepgram",
        input_units=1.0,
    )
    assert record.rated_price_usd == pytest.approx(0.0048 * 1.5)
    assert record.rate_rule == "cost_plus:1.5"


def test_tenant_override_applied_from_context() -> None:
    """The active tenant contextvar selects a per-tenant rule at write time."""
    tracker = CostTracker(
        rate_card=RateCard(
            rules=[
                RateRule(provider="deepgram", markup=1.5),
                RateRule(tenant="acme", markup=1.1),
            ]
        )
    )
    set_tenant("acme")
    try:
        record = tracker.create_record(
            model_id="deepgram/nova-3",
            modality="stt",
            provider="deepgram",
            input_units=1.0,
        )
    finally:
        reset_tenant_id()
    assert record.rated_price_usd == pytest.approx(0.0048 * 1.1)
    assert record.rate_rule == "cost_plus:1.1"


def test_zero_cost_local_model_rates_to_zero() -> None:
    """A free self-hosted model stays free after rating (cost x markup = 0)."""
    tracker = CostTracker(rate_card=RateCard(default_markup=2.0))
    record = tracker.create_record(
        model_id="local/whisper-large-v3",
        modality="stt",
        provider="whisper",
        input_units=1.0,
    )
    assert record.cost_usd == 0.0
    assert record.rated_price_usd == 0.0
