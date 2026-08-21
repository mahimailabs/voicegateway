"""A fixed rate rule must be able to express an LLM contract.

It could not. ``billable_quantity`` summed input and output into one quantity
and multiplied it by one ``unit_price_usd``, so the operator picked a rate and
the other leg was billed at it. Output runs 2.5x to 4x input on every provider
in the catalogue, and a voice agent is prompt-heavy, so the error was large and
in a direction nobody could see from the recorded number.

The fix is three legs. The trap in the fix is that CACHED INPUT IS A SUBSET of
input, not a sibling: LiveKit reports ``prompt_cached_tokens`` as part of
``prompt_tokens``, and ``inference/pricing/llm.py`` clamps on exactly that
contract. Billing input and cached as two independent quantities double-charges
the cached portion, which on an agent with a long stable system prompt is most
of the prompt. The differential test below is what distinguishes the two.
"""

from __future__ import annotations

import pytest

from voicegateway.billing.rate_card import RateCard, RateRule, validate_fixed_pricing
from voicegateway.billing.rating import apply_rule, price, token_leg_price
from voicegateway.inference.pricing.catalog import calculate_cost

# --------------------------------------------------------------------------
# The subset relationship
# --------------------------------------------------------------------------


def _rule(**kw) -> RateRule:
    base = {
        "modality": "llm",
        "kind": "fixed",
        "unit": "1m_token",
        "input_price_usd": 10.0,
        "output_price_usd": 30.0,
    }
    base.update(kw)
    return RateRule(**base)


def test_cached_tokens_are_subtracted_from_input_not_added_to_it() -> None:
    """1M prompt of which 800k cached is 200k uncached, not 1.8M billed."""
    rule = _rule(cached_input_price_usd=1.0)
    got = token_leg_price(
        rule, input_units=1_000_000, output_units=0, cached_input_units=800_000
    )
    # 200k uncached @ $10/M  +  800k cached @ $1/M
    assert got == pytest.approx(2.0 + 0.8)

    # The bug this pins: treating them as siblings bills the cached tokens twice.
    double_counted = (1_000_000 * 10.0 + 800_000 * 1.0) / 1e6
    assert double_counted == pytest.approx(10.8)
    assert got != pytest.approx(double_counted)


def test_a_fully_cached_prompt_bills_entirely_at_the_cached_rate() -> None:
    rule = _rule(cached_input_price_usd=1.0)
    got = token_leg_price(
        rule, input_units=500_000, output_units=0, cached_input_units=500_000
    )
    assert got == pytest.approx(0.5)


def test_cached_units_are_clamped_to_the_prompt_total() -> None:
    """A malformed metric must not produce a negative uncached leg.

    Mirrors the clamp in ``inference/pricing/llm.py`` rather than trusting the
    upstream number.
    """
    rule = _rule(cached_input_price_usd=1.0)
    got = token_leg_price(
        rule, input_units=100_000, output_units=0, cached_input_units=999_999_999
    )
    assert got == pytest.approx(0.1)  # all 100k at the cached rate, never below 0


def test_an_omitted_cached_rate_falls_back_to_the_input_rate() -> None:
    """No negotiated cache discount means cached input bills as ordinary input.

    The alternative default, zero, would silently make cached tokens free and
    understate every prompt-heavy agent.
    """
    rule = _rule()  # no cached_input_price_usd
    assert rule.cached_rate() == pytest.approx(10.0)
    got = token_leg_price(
        rule, input_units=1_000_000, output_units=0, cached_input_units=400_000
    )
    assert got == pytest.approx(10.0)


# --------------------------------------------------------------------------
# The differential test: an operator entering list price must match the catalogue
# --------------------------------------------------------------------------


def _catalogue_legs(model: str, sample: int = 1_000) -> dict[str, float]:
    """The catalogue's own per-1M rates, as an operator would copy them down.

    Sampled at a SMALL token count and scaled up, so a model with
    context-window tiers reports its base rates rather than whichever tier a
    1M-token probe happens to land in. Nothing here hardcodes a price: the
    rates are read from the same catalogue the assertion compares against, so
    the tests hold when list prices move.
    """
    scale = 1_000_000 / sample
    return {
        "input_price_usd": float(calculate_cost("llm", model, input_tokens=sample))
        * scale,
        "cached_input_price_usd": float(
            calculate_cost(
                "llm", model, input_tokens=sample, cached_input_tokens=sample
            )
        )
        * scale,
        "output_price_usd": float(calculate_cost("llm", model, output_tokens=sample))
        * scale,
    }


@pytest.mark.parametrize(
    ("model", "prompt", "completion", "cached"),
    [
        ("openai/gpt-4o", 120_000, 8_000, 90_000),
        ("openai/gpt-4o-mini", 150_000, 12_000, 100_000),
        ("openai/gpt-4o", 50_000, 3_000, 0),
    ],
)
def test_entering_list_price_as_a_rule_reproduces_the_catalogue_total(
    model, prompt, completion, cached
) -> None:
    """The whole point of the change, stated as an equality.

    An operator who types the published rates into a fixed rule must get the
    number the catalogue would have produced. Neither the old sum nor a naive
    three-leg reading satisfies this, which is why it is a differential test
    against the other implementation rather than a hand-computed constant.
    """
    expected = float(
        calculate_cost(
            "llm",
            model,
            input_tokens=prompt,
            output_tokens=completion,
            cached_input_tokens=cached,
        )
    )
    rule = RateRule(
        modality="llm", kind="fixed", unit="1m_token", **_catalogue_legs(model)
    )
    rated = apply_rule(
        rule,
        cost_usd=0.0,
        modality="llm",
        input_units=prompt,
        output_units=completion,
        cached_input_units=cached,
    )
    assert rated.rated_price_usd == pytest.approx(expected)


def test_the_rate_card_cannot_express_context_tiered_pricing() -> None:
    """A known and deliberate limitation, pinned so it is not read as a bug.

    ``claude-sonnet-4-5`` prices every leg higher once the prompt crosses
    200k tokens. A rate card holds ONE rate per leg, so it describes a flat
    contract and must diverge from the catalogue above that boundary.

    The evidence is the SAME model and the SAME rule at two prompt sizes:
    below the boundary the flat rule reproduces the catalogue exactly, above
    it the two part company. That isolates tiering as the cause without
    pinning any dollar amount, so the test survives a price change.

    This is the right trade for operator-entered pricing, which is a
    negotiated flat rate in the ordinary case, but a fixed rule is not a
    general substitute for the catalogue. Expressing tiered contracts would
    need a tier list on the rule, not a fourth leg.
    """
    model = "anthropic/claude-sonnet-4-5"
    rule = RateRule(
        modality="llm", kind="fixed", unit="1m_token", **_catalogue_legs(model)
    )

    def compare(prompt: int, completion: int) -> tuple[float, float]:
        expected = float(
            calculate_cost("llm", model, input_tokens=prompt, output_tokens=completion)
        )
        got = apply_rule(
            rule,
            cost_usd=0.0,
            modality="llm",
            input_units=prompt,
            output_units=completion,
        ).rated_price_usd
        return expected, got

    below_catalogue, below_flat = compare(50_000, 5_000)
    assert below_flat == pytest.approx(below_catalogue)

    above_catalogue, above_flat = compare(800_000, 50_000)
    assert above_flat < above_catalogue


# --------------------------------------------------------------------------
# Load-time validation
# --------------------------------------------------------------------------


def test_a_bare_fixed_price_on_a_token_unit_is_rejected_at_load() -> None:
    """The old shape must fail at deploy, not quietly re-price at runtime.

    Someone may have set the summing behaviour deliberately as an
    approximation. Changing what their number means without telling them
    surfaces in a bill; refusing to load tells them at deploy.
    """
    with pytest.raises(ValueError, match="single 'fixed' price cannot express it"):
        validate_fixed_pricing(modality="llm", unit="1m_token", fixed=5.0)


def test_a_token_rule_needs_both_legs() -> None:
    with pytest.raises(ValueError, match="needs both input_price_usd"):
        validate_fixed_pricing(
            modality="llm", unit="1m_token", fixed=None, input_price_usd=10.0
        )


def test_legs_are_rejected_on_a_single_sided_unit() -> None:
    with pytest.raises(ValueError, match="only apply to token units"):
        validate_fixed_pricing(
            modality="stt", unit="minute", fixed=0.006, input_price_usd=10.0
        )


def test_a_wildcard_modality_is_inferred_from_the_unit_not_rejected() -> None:
    """``{provider: deepgram, fixed: 0.006, unit: minute}`` is a real config.

    A unit names its own modality, so leaving ``modality`` off is unambiguous
    and must keep working. Rejecting it would have broken existing rate cards
    to fix a bug they did not have.
    """
    validate_fixed_pricing(modality="*", unit="minute", fixed=0.006)
    rule = RateRule(
        kind="fixed", provider="deepgram", unit="minute", unit_price_usd=0.006
    )
    assert rule.modality == "stt"


def test_an_inferred_modality_does_not_overwrite_a_stated_one() -> None:
    rule = RateRule(kind="fixed", modality="stt", unit="request", unit_price_usd=0.01)
    assert rule.modality == "stt"


def test_a_cost_plus_rule_keeps_its_wildcard_modality() -> None:
    """Inference is a fixed-rule concern; a markup applies to everything."""
    assert RateRule(kind="cost_plus", markup=1.3).modality == "*"


def test_a_unit_from_the_wrong_modality_is_rejected() -> None:
    with pytest.raises(ValueError, match="not billable for modality"):
        validate_fixed_pricing(modality="tts", unit="minute", fixed=0.006)


def test_request_is_modality_free() -> None:
    validate_fixed_pricing(modality="*", unit="request", fixed=0.01)


# --------------------------------------------------------------------------
# YAML seed + end-to-end
# --------------------------------------------------------------------------


def test_a_token_rule_round_trips_through_yaml_config() -> None:
    card = RateCard.from_config(
        {
            "rules": [
                {
                    "modality": "llm",
                    "provider": "openai",
                    "model": "gpt-4o",
                    "unit": "1m_token",
                    "input_price_usd": 2.5,
                    "cached_input_price_usd": 1.25,
                    "output_price_usd": 10.0,
                }
            ]
        }
    )
    result = price(
        card,
        modality="llm",
        provider="openai",
        model_id="openai/gpt-4o",
        cost_usd=0.0,
        input_units=120_000,
        output_units=8_000,
        cached_input_units=90_000,
    )
    assert result.rated_price_usd == pytest.approx(0.2675)
    assert result.rate_rule == "fixed:in=2.5,cached=1.25,out=10/1m_token"


def test_the_stamped_rule_string_names_every_leg() -> None:
    """``rate_rule`` is the audit trail on each row, so it must show all three.

    ``fixed:2.5/1m_token`` would name one number for a three-number contract,
    which is the labelling failure the legs exist to fix.
    """
    rule = _rule(
        input_price_usd=2.5, cached_input_price_usd=1.25, output_price_usd=10.0
    )
    assert rule.describe() == "fixed:in=2.5,cached=1.25,out=10/1m_token"
