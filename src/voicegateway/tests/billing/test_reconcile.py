"""Margin reconcile + fixed-rule price sync (pure logic)."""

from __future__ import annotations

import pytest

from voicegateway.billing.rate_card import RateCard, RateRule
from voicegateway.billing.reconcile import (
    margin_reconcile,
    sync_fixed_rules,
)


def _usage(tenant, cost, rated):
    margin = rated - cost
    pct = (margin / rated * 100.0) if rated else 0.0
    return {
        "tenant_id": tenant,
        "requests": 1,
        "cost_usd": cost,
        "rated_usd": rated,
        "margin_usd": margin,
        "margin_pct": pct,
    }


def test_margin_reconcile_flags_negative_and_thin() -> None:
    rows = [
        _usage("healthy", cost=0.10, rated=0.20),  # 50% margin -> ok
        _usage("thin", cost=0.95, rated=1.00),  # 5% margin -> thin
        _usage("loss", cost=0.30, rated=0.20),  # negative
    ]
    lines = margin_reconcile(rows, thin_pct=20.0)
    flags = {ln.tenant_id: ln.flag for ln in lines}
    assert flags == {"healthy": "", "thin": "thin", "loss": "negative"}


def test_sync_fixed_rules_flags_thin_margin() -> None:
    card = RateCard(
        rules=[
            RateRule(
                provider="deepgram",
                model="nova-3",
                modality="stt",
                kind="fixed",
                unit_price_usd=0.0060,
                unit="minute",
            ),
            RateRule(provider="openai", markup=1.5),  # cost_plus, skipped
        ]
    )
    # Base moved up to $0.0059/min: only $0.0001 margin on a $0.0060 price.
    lines = sync_fixed_rules(card, lambda _rule: 0.0059, thin_pct=20.0)
    assert len(lines) == 1  # cost_plus rule not included
    line = lines[0]
    assert line.scope == "deepgram/nova-3 stt"
    assert line.base_cost == pytest.approx(0.0059)
    assert line.margin_usd == pytest.approx(0.0001)
    assert line.flag == "thin"


def test_sync_fixed_rules_marks_unresolvable_base() -> None:
    card = RateCard(
        rules=[
            RateRule(
                modality="llm",
                kind="fixed",
                unit_price_usd=0.01,
                unit="1m_token",
            )
        ]
    )
    lines = sync_fixed_rules(card, lambda _rule: None)
    assert lines[0].flag == "unresolvable"
    assert lines[0].base_cost is None


def test_sync_fixed_rules_healthy_margin_not_flagged() -> None:
    card = RateCard(
        rules=[
            RateRule(
                provider="deepgram",
                model="nova-3",
                modality="stt",
                kind="fixed",
                unit_price_usd=0.0060,
                unit="minute",
            )
        ]
    )
    lines = sync_fixed_rules(card, lambda _rule: 0.0030)  # 50% margin
    assert lines[0].flag == ""
