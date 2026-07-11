"""Unit tests for the rate-card model and resolution.

The rate card is the price book: a list of :class:`RateRule` rows, each
scoped by (tenant, plan) on one axis and (modality, provider, model) on
another. Resolution picks the single most specific matching rule, with
tenant beating plan beating global, and model beating provider beating
modality-only. Later rows win ties so DB overrides layered after the YAML
seed take precedence.
"""

from __future__ import annotations

from voicegateway.billing.rate_card import RateCard, RateRule

# ----- RateRule.matches -------------------------------------------------


def test_wildcard_rule_matches_anything() -> None:
    rule = RateRule()  # all wildcards, cost_plus default
    assert rule.matches(
        modality="stt",
        provider="deepgram",
        model_id="deepgram/nova-3",
        tenant=None,
        plan=None,
    )


def test_provider_rule_matches_only_that_provider() -> None:
    rule = RateRule(provider="openai")
    assert rule.matches(
        modality="llm",
        provider="openai",
        model_id="openai/gpt-4o",
        tenant="acme",
        plan="pro",
    )
    assert not rule.matches(
        modality="llm",
        provider="anthropic",
        model_id="anthropic/claude",
        tenant="acme",
        plan="pro",
    )


def test_model_rule_matches_bare_or_full_model_id() -> None:
    rule = RateRule(model="nova-3")
    assert rule.matches(
        modality="stt",
        provider="deepgram",
        model_id="deepgram/nova-3",
        tenant=None,
        plan=None,
    )
    full = RateRule(model="deepgram/nova-3")
    assert full.matches(
        modality="stt",
        provider="deepgram",
        model_id="deepgram/nova-3",
        tenant=None,
        plan=None,
    )


def test_tenant_rule_does_not_match_other_tenants() -> None:
    rule = RateRule(tenant="acme")
    assert rule.matches(
        modality="tts",
        provider="cartesia",
        model_id="cartesia/sonic",
        tenant="acme",
        plan=None,
    )
    assert not rule.matches(
        modality="tts",
        provider="cartesia",
        model_id="cartesia/sonic",
        tenant="globex",
        plan=None,
    )
    # A global (tenant=None) rule still matches a request that has a tenant.
    glob = RateRule()
    assert glob.matches(
        modality="tts",
        provider="cartesia",
        model_id="cartesia/sonic",
        tenant="acme",
        plan=None,
    )


# ----- specificity ordering --------------------------------------------


def test_specificity_orders_tenant_over_model_over_provider() -> None:
    tenant_rule = RateRule(tenant="acme")
    model_rule = RateRule(model="nova-3")
    provider_rule = RateRule(provider="deepgram")
    modality_rule = RateRule(modality="stt")
    glob = RateRule()
    assert tenant_rule.specificity() > model_rule.specificity()
    assert model_rule.specificity() > provider_rule.specificity()
    assert provider_rule.specificity() > modality_rule.specificity()
    assert modality_rule.specificity() > glob.specificity()


# ----- RateCard.resolve -------------------------------------------------


def test_resolve_picks_most_specific_match() -> None:
    card = RateCard(
        rules=[
            RateRule(markup=1.3),  # global default
            RateRule(provider="deepgram", markup=1.5),
            RateRule(model="nova-3", markup=2.0),
        ]
    )
    hit = card.resolve(modality="stt", provider="deepgram", model_id="deepgram/nova-3")
    assert hit is not None
    assert hit.markup == 2.0  # model rule beats provider + global


def test_resolve_tenant_override_beats_global_model_rule() -> None:
    card = RateCard(
        rules=[
            RateRule(model="nova-3", markup=2.0),
            RateRule(tenant="acme", markup=1.1),
        ]
    )
    hit = card.resolve(
        modality="stt",
        provider="deepgram",
        model_id="deepgram/nova-3",
        tenant="acme",
    )
    assert hit is not None
    assert hit.markup == 1.1  # tenant scope outranks a more specific global rule


def test_resolve_returns_none_when_no_rule_matches() -> None:
    card = RateCard(rules=[RateRule(provider="openai", markup=1.5)])
    assert (
        card.resolve(modality="stt", provider="deepgram", model_id="deepgram/nova-3")
        is None
    )


def test_resolve_later_rule_wins_on_specificity_tie() -> None:
    # Two equally specific provider rules; the later one (a DB override
    # appended after the seed) must win.
    card = RateCard(
        rules=[
            RateRule(provider="deepgram", markup=1.5),
            RateRule(provider="deepgram", markup=1.9),
        ]
    )
    hit = card.resolve(modality="stt", provider="deepgram", model_id="deepgram/nova-3")
    assert hit is not None
    assert hit.markup == 1.9


# ----- from_config ------------------------------------------------------


def test_from_config_none_yields_empty_passthrough_card() -> None:
    card = RateCard.from_config(None)
    assert card.rules == []
    assert card.default_markup == 1.0


def test_from_config_parses_default_markup_and_rules() -> None:
    card = RateCard.from_config(
        {
            "default_markup": 1.3,
            "rules": [
                {"provider": "openai", "markup": 1.5},
                {
                    "modality": "stt",
                    "provider": "deepgram",
                    "model": "nova-3",
                    "fixed": 0.0060,
                    "unit": "minute",
                },
                {"tenant": "acme", "markup": 1.1},
            ],
        }
    )
    assert card.default_markup == 1.3
    assert len(card.rules) == 3
    fixed = next(r for r in card.rules if r.kind == "fixed")
    assert fixed.unit_price_usd == 0.0060
    assert fixed.unit == "minute"
    assert fixed.provider == "deepgram"
    cost_plus = next(r for r in card.rules if r.provider == "openai")
    assert cost_plus.kind == "cost_plus"
    assert cost_plus.markup == 1.5
