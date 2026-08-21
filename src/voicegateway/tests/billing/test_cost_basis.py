"""A rate rule can say what the operator PAYS, not only what they charge.

Before this, ``rating.price()`` took ``cost_usd`` as an input and only ever
turned it into ``rated_price_usd``. Nothing could replace ``cost_usd`` itself,
so it was always the voice-prices catalogue figure: a published list price.
Anyone at volume is on a negotiated contract that differs from it by a margin
nobody outside the contract can see, so ``cost_plus`` marked up a number that
was never true and produced a margin that was wrong in a direction the
operator had no way to detect.

The workaround was a ``fixed`` rule carrying the negotiated cost. That gives a
correct total, and lands it in the SELL column while ``cost_usd`` keeps lying,
so any report comparing the two is nonsense.

THE DESIGN DECISION THIS FILE PINS is that cost and price resolve
INDEPENDENTLY. The ordinary configuration is a model-specific cost ("this is
what I pay for nova-3") plus a global markup ("charge 1.3x"). A single
most-specific-wins pass over one merged list lets the cost rule win outright
and silently drops the markup, producing a bill at cost with nothing in the
output saying so. Two resolutions, one per side.
"""

from __future__ import annotations

import pytest

from voicegateway.billing.rate_card import RateCard, RateRule, validate_sets
from voicegateway.billing.rating import declared_cost, price
from voicegateway.middleware.cost_tracker_middleware import CostTracker

_SCOPE = {"modality": "stt", "provider": "deepgram", "model_id": "deepgram/nova-3"}


def _cost_rule(**kw) -> RateRule:
    base = {
        "sets": "cost",
        "kind": "fixed",
        "modality": "stt",
        "provider": "deepgram",
        "model": "nova-3",
        "unit": "minute",
        "unit_price_usd": 0.0035,
    }
    base.update(kw)
    return RateRule(**base)


# --------------------------------------------------------------------------
# The two resolutions
# --------------------------------------------------------------------------


def test_a_model_cost_and_a_global_markup_both_apply() -> None:
    """The case a single resolution silently breaks.

    Deepgram list is $0.0048/min. The operator negotiated $0.0035 and charges
    1.3x. Correct is 10 min at $0.035 cost and $0.0455 billed. A merged
    resolution returns the model-specific COST rule as most specific, applies
    it as though it were the price rule, and bills $0.035: cost exactly, no
    margin, no error.
    """
    card = RateCard(
        rules=[_cost_rule(), RateRule(sets="price", kind="cost_plus", markup=1.3)]
    )
    cost, cost_rule = declared_cost(card, **_SCOPE, input_units=10.0)
    assert cost == pytest.approx(0.035)
    assert cost_rule.sets == "cost"

    rated = price(card, **_SCOPE, cost_usd=cost, input_units=10.0)
    assert rated.rated_price_usd == pytest.approx(0.0455)
    assert rated.rate_rule == "cost_plus:1.3"


def test_resolving_one_side_never_returns_the_other() -> None:
    card = RateCard(rules=[_cost_rule()])
    assert card.resolve(**_SCOPE, sets="cost") is not None
    assert card.resolve(**_SCOPE, sets="price") is None


def test_no_cost_rule_means_no_declared_cost() -> None:
    card = RateCard(rules=[RateRule(sets="price", kind="cost_plus", markup=1.3)])
    assert declared_cost(card, **_SCOPE, input_units=10.0) is None


def test_a_cost_rule_can_price_an_llm_by_leg() -> None:
    """The negotiated LLM contract, which is three numbers, not one."""
    card = RateCard(
        rules=[
            RateRule(
                sets="cost",
                kind="fixed",
                modality="llm",
                provider="openai",
                model="gpt-4o",
                unit="1m_token",
                input_price_usd=2.0,
                cached_input_price_usd=1.0,
                output_price_usd=8.0,
            )
        ]
    )
    cost, _ = declared_cost(
        card,
        modality="llm",
        provider="openai",
        model_id="openai/gpt-4o",
        input_units=1_000_000,
        output_units=100_000,
        cached_input_units=800_000,
    )
    # 200k uncached @ $2/M + 800k cached @ $1/M + 100k output @ $8/M
    assert cost == pytest.approx(0.4 + 0.8 + 0.8)


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def test_a_cost_rule_cannot_be_cost_plus() -> None:
    """``cost_plus`` multiplies a recorded cost, so it cannot produce one.

    The only number available to multiply would be the catalogue figure this
    rule exists to replace, so the result would be a markup on a list price
    wearing the label of a negotiated cost.
    """
    with pytest.raises(ValueError, match="cannot produce one"):
        validate_sets("cost", "cost_plus")


def test_an_unknown_side_is_rejected() -> None:
    with pytest.raises(ValueError, match="sets one of"):
        validate_sets("revenue", "fixed")


def test_yaml_rejects_a_cost_rule_with_a_markup() -> None:
    with pytest.raises(ValueError, match="cannot produce one"):
        RateCard.from_config(
            {"rules": [{"sets": "cost", "provider": "deepgram", "markup": 1.2}]}
        )


def test_rules_default_to_setting_the_price() -> None:
    """Every rule written before ``sets`` existed set the price, definitionally."""
    card = RateCard.from_config({"rules": [{"provider": "openai", "markup": 1.5}]})
    assert card.rules[0].sets == "price"
    assert RateRule().sets == "price"


# --------------------------------------------------------------------------
# The recorded row
# --------------------------------------------------------------------------


def test_the_row_records_the_declared_cost_and_names_the_rule() -> None:
    """``cost_usd`` stops being the catalogue's number, and says so.

    Without this the operator's negotiated rate could only reach the sell
    column, leaving ``cost_usd`` at list price and any cost-vs-revenue report
    comparing a real number against an invented one.
    """
    card = RateCard(
        rules=[_cost_rule(), RateRule(sets="price", kind="cost_plus", markup=1.3)]
    )
    record = CostTracker(rate_card=card).create_record(
        model_id="deepgram/nova-3",
        modality="stt",
        provider="deepgram",
        input_units=10.0,
    )
    assert record.cost_usd == pytest.approx(0.035)
    assert record.pricing_source.startswith("rate-card:")
    assert record.rated_price_usd == pytest.approx(0.0455)


def test_without_a_cost_rule_the_catalogue_still_prices_the_row() -> None:
    """The override is additive: nothing changes for anyone not using it."""
    record = CostTracker().create_record(
        model_id="deepgram/nova-3",
        modality="stt",
        provider="deepgram",
        input_units=10.0,
    )
    assert record.cost_usd == pytest.approx(0.048)
    assert record.pricing_source.startswith("voice-prices@")


def test_a_self_hosted_model_is_never_overridden_by_a_wildcard_cost_rule() -> None:
    """``local/*`` runs on hardware already paid for; a cloud rate is not it.

    A broad cost rule is easy to write, and silently attaching a per-minute
    cloud rate to a local model would invent spend that never happened.
    """
    card = RateCard(
        rules=[
            RateRule(
                sets="cost",
                kind="fixed",
                modality="stt",
                unit="minute",
                unit_price_usd=0.01,
            )
        ]
    )
    record = CostTracker(rate_card=card).create_record(
        model_id="local/whisper", modality="stt", provider="local", input_units=10.0
    )
    assert record.cost_usd == 0.0
    assert record.pricing_source == "voicegateway-local"


def test_the_collector_re_derives_cost_on_ingest() -> None:
    """Agents record the catalogue figure; the collector holds the contract.

    An agent carries no rate card, so it writes the list price. The collector
    is the source of truth for what things cost, so an ingested row is
    corrected before the markup is applied, and a margin is never computed
    against a price the operator does not pay.
    """
    agent = CostTracker()
    record = agent.create_record(
        model_id="deepgram/nova-3",
        modality="stt",
        provider="deepgram",
        input_units=10.0,
    )
    assert record.cost_usd == pytest.approx(0.048)  # list price, as the agent saw it

    collector = CostTracker(
        rate_card=RateCard(
            rules=[_cost_rule(), RateRule(sets="price", kind="cost_plus", markup=1.3)]
        )
    )
    collector.rate_record(record)
    assert record.cost_usd == pytest.approx(0.035)
    assert record.pricing_source.startswith("rate-card:")
    assert record.rated_price_usd == pytest.approx(0.0455)


def test_a_db_rule_names_itself_by_id_rather_than_by_its_price() -> None:
    """The audit trail has to identify the RULE, not restate its number.

    Two rules at different scopes can carry the same rate, so a row stamped
    only with the price cannot say which entry produced it, which is exactly
    what reconcile needs when an invoice disagrees.
    """
    rule = _cost_rule(rule_id="cost|*|*|stt|deepgram|nova-3")
    assert rule.audit_token() == "cost|*|*|stt|deepgram|nova-3"
    assert _cost_rule().audit_token() == "fixed:0.0035/minute"
