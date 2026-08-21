"""A reconcile diff has to say what is wrong, not only that something is.

``voicegw reconcile`` compared VG's per-model totals against a provider export
and flagged rows past a threshold. That is enough when the cost came from a
catalogue everyone can inspect. It stopped being enough once rates are
operator-entered, because a hand-typed rule has NO oracle inside the product:
a rate entered as 0.008 instead of 0.08 produces a perfectly plausible bill,
and the invoice is the only thing that can catch it.

A flagged row now names which of three things disagrees, because they have
three different fixes and only one is in the operator's hands:

    coverage   one side has the model and the other does not
    units      the two sides metered different amounts of work
    rate       the counts agree and the money does not

For a rate disagreement it names the rule that produced VG's figure and the
per-unit rate the invoice implies, which is the edit rather than a hint at it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voicegateway.services import reconciliation_service as rs


def _vg_row(model: str, *, units: float, cost: float, source: str) -> dict:
    """One request as ``get_requests_in_window`` returns it (deepgram: minutes)."""
    return {
        "model_id": f"deepgram/{model}",
        "modality": "stt",
        "input_units": units,
        "output_units": 0.0,
        "cost_usd": cost,
        "pricing_source": source,
    }


def _provider_file(tmp_path: Path, model: str, *, seconds: float, cost: float) -> Path:
    path = tmp_path / "usage.json"
    path.write_text(
        json.dumps([{"model": model, "audio_seconds": seconds, "cost_usd": cost}])
    )
    return path


def _line(lines: list[rs.ReconcileLine], model: str) -> rs.ReconcileLine:
    return next(ln for ln in lines if ln.model == model)


# --------------------------------------------------------------------------
# The three causes
# --------------------------------------------------------------------------


def test_a_mistyped_rate_is_diagnosed_as_a_rate_problem(tmp_path) -> None:
    """The failure operator-declared pricing introduces, and cannot self-catch.

    10 minutes metered on both sides. The operator typed 0.00035 where they
    meant 0.0035, so VG says $0.0035 and the invoice says $0.035. The units
    agree exactly, which is what makes this a rate fault rather than a
    metering one.
    """
    vg = [_vg_row("nova-3", units=10.0, cost=0.0035, source="rate-card:cost|mine")]
    lines = rs.reconcile(
        "deepgram", vg, _provider_file(tmp_path, "nova-3", seconds=600.0, cost=0.035)
    )
    line = _line(lines, "nova-3")
    assert line.flagged
    assert line.cause == "rate"
    assert line.units_diff_pct == pytest.approx(0.0)
    # The rate the invoice implies is what the rule should have said.
    assert line.vg_rate == pytest.approx(0.0035 / 600.0)
    assert line.provider_rate == pytest.approx(0.035 / 600.0)


def test_a_metering_gap_is_not_blamed_on_the_rate(tmp_path) -> None:
    """Half the sessions never reached VG. No rate edit closes that.

    Telling an operator to check their rate here would send them to change a
    number that is correct.
    """
    vg = [_vg_row("nova-3", units=5.0, cost=0.0175, source="rate-card:cost|mine")]
    lines = rs.reconcile(
        "deepgram", vg, _provider_file(tmp_path, "nova-3", seconds=600.0, cost=0.035)
    )
    line = _line(lines, "nova-3")
    assert line.flagged
    assert line.cause == "units"


def test_a_model_on_only_one_side_is_a_coverage_problem(tmp_path) -> None:
    lines = rs.reconcile(
        "deepgram", [], _provider_file(tmp_path, "nova-3", seconds=600.0, cost=0.035)
    )
    line = _line(lines, "nova-3")
    assert line.cause == "coverage"
    assert line.matched_in_vg is False


def test_an_agreeing_row_is_not_diagnosed_at_all(tmp_path) -> None:
    """No cause on a row that is fine, so the report stays quiet when it can."""
    vg = [_vg_row("nova-3", units=10.0, cost=0.035, source="rate-card:cost|mine")]
    lines = rs.reconcile(
        "deepgram", vg, _provider_file(tmp_path, "nova-3", seconds=600.0, cost=0.035)
    )
    line = _line(lines, "nova-3")
    assert not line.flagged
    assert line.cause == ""


# --------------------------------------------------------------------------
# Naming the suspect
# --------------------------------------------------------------------------


def test_the_report_names_the_rate_card_rule_that_produced_the_number(
    tmp_path,
) -> None:
    """Point at the entry, not at the usage.

    The operator needs to know WHICH rule to edit. Two rules at different
    scopes can carry the same rate, so restating the price identifies nothing.
    """
    vg = [
        _vg_row(
            "nova-3",
            units=10.0,
            cost=0.0035,
            source="rate-card:cost|*|*|stt|deepgram|nova-3",
        )
    ]
    lines = rs.reconcile(
        "deepgram", vg, _provider_file(tmp_path, "nova-3", seconds=600.0, cost=0.035)
    )
    text = "\n".join(rs.explain(lines))
    assert "rate-card rule 'cost|*|*|stt|deepgram|nova-3'" in text
    assert "10.00x" in text, text  # the invoice is 10x what was typed


def test_the_report_says_when_the_catalogue_produced_the_number(tmp_path) -> None:
    """A catalogue disagreement is not the operator's rule to fix.

    It means the published rate is stale or their contract differs from list,
    and the remedy is to declare a cost rule rather than to edit one.
    """
    vg = [_vg_row("nova-3", units=10.0, cost=0.0035, source="voice-prices@0.6.0")]
    lines = rs.reconcile(
        "deepgram", vg, _provider_file(tmp_path, "nova-3", seconds=600.0, cost=0.035)
    )
    text = "\n".join(rs.explain(lines))
    assert "the catalog (voice-prices@0.6.0)" in text
    assert "rate-card rule" not in text


def test_the_report_says_when_nothing_priced_the_rows(tmp_path) -> None:
    vg = [_vg_row("nova-3", units=10.0, cost=0.0, source="")]
    lines = rs.reconcile(
        "deepgram", vg, _provider_file(tmp_path, "nova-3", seconds=600.0, cost=0.035)
    )
    text = "\n".join(rs.explain(lines))
    assert "never priced" in text


def test_a_units_disagreement_says_no_rate_change_will_close_it(tmp_path) -> None:
    vg = [_vg_row("nova-3", units=5.0, cost=0.0175, source="rate-card:cost|mine")]
    lines = rs.reconcile(
        "deepgram", vg, _provider_file(tmp_path, "nova-3", seconds=600.0, cost=0.035)
    )
    text = "\n".join(rs.explain(lines))
    assert "metering gap" in text
    assert "No rate change closes it" in text


# --------------------------------------------------------------------------
# The output surfaces carry it
# --------------------------------------------------------------------------


def test_the_text_report_appends_the_guidance_only_when_something_is_flagged(
    tmp_path,
) -> None:
    clean = rs.reconcile(
        "deepgram",
        [_vg_row("nova-3", units=10.0, cost=0.035, source="voice-prices@0.6.0")],
        _provider_file(tmp_path, "nova-3", seconds=600.0, cost=0.035),
    )
    assert "What to check:" not in rs.format_text(clean, "deepgram")

    dirty = rs.reconcile(
        "deepgram",
        [_vg_row("nova-3", units=10.0, cost=0.0035, source="rate-card:cost|mine")],
        _provider_file(tmp_path, "nova-3", seconds=600.0, cost=0.035),
    )
    assert "What to check:" in rs.format_text(dirty, "deepgram")


def test_csv_and_json_carry_the_cause(tmp_path) -> None:
    """A machine reader must get the diagnosis too, not just the terminal."""
    lines = rs.reconcile(
        "deepgram",
        [_vg_row("nova-3", units=10.0, cost=0.0035, source="rate-card:cost|mine")],
        _provider_file(tmp_path, "nova-3", seconds=600.0, cost=0.035),
    )
    csv_out = rs.format_csv(lines)
    assert "cause" in csv_out.splitlines()[0]
    assert "rate" in csv_out.splitlines()[1]

    doc = json.loads(rs.format_json(lines, provider="deepgram"))
    row = doc["rows"][0]
    assert row["cause"] == "rate"
    assert row["pricing_sources"] == ["rate-card:cost|mine"]
    assert row["provider_rate"] > row["vg_rate"]
