"""A model can match the catalogue and still carry no rate.

``deepgram/nova-general`` matches the catalogue entry ``nova``, whose
``ModelPrice`` has every field ``None``. ``calc_price`` raises nothing, applies
nothing, and returns a total of ``Decimal('0')``. Before this change the row
was stamped ``voice-prices@<version>``, which asserts the catalogue priced it.
It did not. Downstream the row was indistinguishable from a model that
genuinely costs nothing, and it was the only zero in the system that logged no
warning.

That is the fourth state the three-state design could not express:

    voice-prices@<version>   priced, rate-backed
    voicegateway-local       free because it runs on your hardware
    ""                       unknown model, nothing to price it with
    voice-prices-unrated     matched, and no rate was applied      <- this

The tag deliberately makes no claim about WHY there is no rate. In
voice-prices 0.6.0, 139 catalogue entries are rateless and 131 of them are
``:free`` models where zero is correct; the catalogue records no field
separating those from the 8 that are simply missing a rate. A meter that
cannot tell them apart must not pick one, so it reports what it observed.

These tests skip rather than fail when the installed catalogue has no rateless
entry, because whether one exists is a fact about the catalogue rather than
about this code. voice-prices 0.3.0 raises ``LookupError`` for these refs and
takes the honest path already.
"""

from __future__ import annotations

import logging

import pytest

from voicegateway.inference.pricing import catalog
from voicegateway.middleware.cost_tracker_middleware import CostTracker

# A rateless match if the installed catalogue has one, checked at runtime.
_CANDIDATES = ("deepgram/nova-general", "deepgram/whisper-tiny")


def _rateless_model() -> str | None:
    for ref in _CANDIDATES:
        _, unrated = catalog.calculate_cost_detail("stt", ref, audio_seconds=60)
        if unrated:
            return ref
    return None


@pytest.fixture
def rateless() -> str:
    model = _rateless_model()
    if model is None:
        pytest.skip("installed voice-prices has no rateless STT entry to exercise")
    return model


# --------------------------------------------------------------------------
# The catalogue facade reports the distinction
# --------------------------------------------------------------------------


def test_a_rateless_match_reports_the_units_it_could_not_price(rateless) -> None:
    cost, unrated = catalog.calculate_cost_detail("stt", rateless, audio_seconds=600)
    assert cost == 0
    assert unrated, "a rateless match must name the units it could not price"


def test_a_rated_model_reports_nothing_unrated() -> None:
    cost, unrated = catalog.calculate_cost_detail(
        "stt", "deepgram/nova-3", audio_seconds=600
    )
    assert cost and cost > 0
    assert unrated == ()


def test_an_unknown_model_is_still_none_not_a_rateless_zero() -> None:
    """The two must not collapse: they have different remedies.

    An unknown model needs a catalogue entry. A rateless match has an entry
    already and needs a rate on it.
    """
    cost, unrated = catalog.calculate_cost_detail(
        "stt", "nobody/made-this-up", audio_seconds=600
    )
    assert cost is None
    assert unrated == ()


# --------------------------------------------------------------------------
# The recorded row stops claiming provenance it does not have
# --------------------------------------------------------------------------


def test_the_row_is_tagged_unrated_rather_than_priced(rateless) -> None:
    record = CostTracker().create_record(
        model_id=rateless, modality="stt", provider="deepgram", input_units=10.0
    )
    assert record.cost_usd == 0.0
    assert record.pricing_source == catalog.UNRATED_SOURCE
    assert not record.pricing_source.startswith("voice-prices@"), (
        "the tag must not read as a priced row to anything matching on the "
        "voice-prices@ prefix"
    )


def test_the_four_states_are_all_distinguishable(rateless) -> None:
    """The point of the change, stated as four different strings."""
    tracker = CostTracker()

    def source(model: str) -> str:
        return tracker.create_record(
            model_id=model,
            modality="stt",
            provider=model.split("/")[0],
            input_units=10.0,
        ).pricing_source

    priced = source("deepgram/nova-3")
    unrated = source(rateless)
    unknown = source("nobody/made-this-up")
    self_hosted = source("local/whisper")

    assert len({priced, unrated, unknown, self_hosted}) == 4
    assert priced.startswith("voice-prices@")
    assert unrated == catalog.UNRATED_SOURCE
    assert unknown == ""
    assert self_hosted == catalog.SELF_HOSTED_SOURCE


def test_a_rateless_match_warns(rateless, caplog) -> None:
    """It was the only zero in the system that logged nothing.

    The warning names the units, because the remedy is to put a rate on an
    entry that already exists rather than to add the model.
    """
    with caplog.at_level(logging.WARNING):
        CostTracker().create_record(
            model_id=rateless, modality="stt", provider="deepgram", input_units=10.0
        )
    messages = [r.getMessage() for r in caplog.records]
    assert any("carries no rate" in m for m in messages), messages
    assert any(rateless in m for m in messages), messages


def test_a_priced_model_does_not_warn(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        CostTracker().create_record(
            model_id="deepgram/nova-3",
            modality="stt",
            provider="deepgram",
            input_units=10.0,
        )
    assert not caplog.records
