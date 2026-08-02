"""The ramp plan an operator declares, and the weight it may carry.

Every imported test row carries ``target_concurrency: None``, so
``derive_calls_per_node`` refuses across a multi-step ramp. That refusal is
correct: the generator records what each step REACHED, never what it was ASKING
for, and those differ precisely when the run plateaued.

So the operator can declare the plan. **A declaration is not a measurement**, and
this file exists to keep the difference load-bearing rather than decorative:

* a declared target the run did not reach refuses the derivation and reports a
  plateau, rather than sizing a fleet against a number nobody demonstrated
* anything derived from a declaration is labelled as such in the payload
* with no plan supplied, capacity still refuses. This adds an input; it does not
  lower the bar
* the peak each step reached is measured and is never overwritten by what was
  asked for
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer

from voicegateway.cli.loadtest_cli import _capacity_for, _plan_from_file
from voicegateway.livekit_diag.gates import MAX_NODE_CPU_UTILISATION
from voicegateway.loadtest.capacity import RampStep, derive_calls_per_node
from voicegateway.loadtest.importer import plan_from_notes, plan_to_notes


def _rows(*triples):
    return [
        {
            "name": f"ramp-{target}",
            "target_concurrency": target,
            "peak_concurrency": peak,
            "peak_cpu_utilisation": cpu,
        }
        for target, peak, cpu in triples
    ]


# A ramp that reached every target and crossed the ceiling on the last step.
SATURATING = _rows(
    (100, 100, 0.31), (150, 150, 0.48), (200, 200, 0.66), (250, 250, 0.79)
)


def _write(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(payload))
    return path


# --------------------------------------------------------------------------
# A declared target the run did not reach refuses
# --------------------------------------------------------------------------


def test_a_target_above_the_reached_peak_refuses_with_a_plateau() -> None:
    """The concrete example the invariant asks for.

    The step asked for 500 and reached 250. Something stopped scaling before the
    target, and whether that was the node or the generator is exactly what a
    capacity figure would have to assume. It refuses instead.
    """
    short = _rows(
        (100, 100, 0.31), (150, 150, 0.48), (200, 200, 0.66), (500, 250, 0.79)
    )
    capacity = _capacity_for(short)
    assert capacity["calls_per_node"] is None
    assert "did not reach the concurrency they asked for" in capacity["reason"]
    assert "asked 500, reached 250" in capacity["reason"]
    assert "tiers" not in capacity


def test_the_same_ramp_derives_once_the_targets_are_reached() -> None:
    """Non-vacuous: the refusal is the shortfall, not the shape of the ramp."""
    capacity = _capacity_for(SATURATING)
    assert capacity["calls_per_node"] == 200


def test_a_small_shortfall_inside_tolerance_still_derives() -> None:
    """248 of 250 is the generator rounding, not a ceiling."""
    near = _rows((100, 100, 0.31), (150, 150, 0.48), (200, 200, 0.66), (250, 248, 0.79))
    assert _capacity_for(near)["calls_per_node"] == 200


def test_the_declared_target_never_replaces_the_reached_peak() -> None:
    """The peak is measured. A declaration must not stand in for it.

    Asserted through the derivation: the figure comes out as the highest
    concurrency REACHED under the ceiling, never the target that was asked for.
    """
    reached = _rows(
        (100, 100, 0.31), (150, 150, 0.48), (200, 195, 0.66), (250, 250, 0.79)
    )
    # 195 is inside tolerance of 200, so this derives, and the answer is the
    # measured 195 rather than the declared 200.
    assert _capacity_for(reached)["calls_per_node"] == 195


# --------------------------------------------------------------------------
# What rests on a declaration says so
# --------------------------------------------------------------------------


def test_a_derived_table_is_labelled_as_resting_on_a_declaration() -> None:
    capacity = _capacity_for(SATURATING)
    assert capacity["rests_on_declaration"] is True
    assert capacity["declared_steps"] == [
        "ramp-100",
        "ramp-150",
        "ramp-200",
        "ramp-250",
    ]
    assert "not measured" in capacity["declaration_basis"]


def test_the_label_is_present_even_when_the_derivation_refuses() -> None:
    """A refusal still rested on declared inputs, and says so."""
    short = _rows((100, 100, 0.31), (500, 250, 0.79))
    capacity = _capacity_for(short)
    assert capacity["calls_per_node"] is None
    assert capacity["rests_on_declaration"] is True


def test_an_undeclared_run_carries_no_label_and_no_figure() -> None:
    """This adds an input; it does not lower the bar."""
    undeclared = [
        {
            "name": "a",
            "target_concurrency": None,
            "peak_concurrency": 100,
            "peak_cpu_utilisation": 0.31,
        },
        {
            "name": "b",
            "target_concurrency": None,
            "peak_concurrency": 250,
            "peak_cpu_utilisation": 0.79,
        },
    ]
    capacity = _capacity_for(undeclared)
    assert capacity["calls_per_node"] is None
    assert "rests_on_declaration" not in capacity
    assert "could not be ruled out" in capacity["reason"]


# --------------------------------------------------------------------------
# The plan file
# --------------------------------------------------------------------------


def test_the_short_form_still_works(tmp_path: Path) -> None:
    assert _plan_from_file(_write(tmp_path, {"ramp-100": 100})) == {
        "ramp-100": {"target_concurrency": 100}
    }


def test_the_long_form_carries_rate_and_hold(tmp_path: Path) -> None:
    """The rate is what lets the plan be checked BEFORE the run."""
    plan = _plan_from_file(
        _write(
            tmp_path,
            {
                "ramp-500": {
                    "target_concurrency": 500,
                    "rate_per_second": 4.17,
                    "hold_seconds": 120,
                }
            },
        )
    )
    assert plan == {
        "ramp-500": {
            "target_concurrency": 500,
            "rate_per_second": 4.17,
            "hold_seconds": 120.0,
        }
    }


def test_an_unreachable_plan_is_caught_from_the_rate_alone() -> None:
    """500 concurrent at 1/s over 60s cannot happen, whatever the node does."""
    steps = [
        RampStep(
            target_concurrency=500,
            peak_concurrency=60,
            peak_cpu_utilisation=0.4,
            rate_per_second=1.0,
            hold_seconds=60.0,
        )
    ]
    figure, reason = derive_calls_per_node(steps, cpu_ceiling=MAX_NODE_CPU_UTILISATION)
    assert figure is None
    assert "cannot reach" in reason


@pytest.mark.parametrize(
    "bad",
    [
        {"ramp-1": 0},
        {"ramp-1": -5},
        {"ramp-1": "many"},
        {"ramp-1": {"target_concurrency": 100, "rate_per_second": 0}},
        {"ramp-1": {"target_concurrency": 100, "rate_per_second": "fast"}},
    ],
)
def test_a_malformed_plan_is_refused(tmp_path: Path, bad) -> None:
    with pytest.raises(typer.BadParameter):
        _plan_from_file(_write(tmp_path, bad))


def test_a_misspelled_field_is_refused_not_ignored(tmp_path: Path) -> None:
    """Silently ignoring it would leave the declaration quietly incomplete."""
    with pytest.raises(typer.BadParameter) as excinfo:
        _plan_from_file(
            _write(tmp_path, {"ramp-1": {"target_concurrency": 100, "rate": 4}})
        )
    assert "rate" in str(excinfo.value)


# --------------------------------------------------------------------------
# It survives to report time
# --------------------------------------------------------------------------


def test_the_plan_round_trips_through_the_run_notes() -> None:
    """The report is exported by a later command reading the row.

    rate_per_second has no column, so a declaration that did not survive here
    would be lost between import and export.
    """
    plan = {
        "ramp-100": {"target_concurrency": 100, "rate_per_second": 3.23},
        "ramp-500": {"target_concurrency": 500},
    }
    notes = "artifact sha256: " + "a" * 64 + plan_to_notes(plan)
    assert plan_from_notes(notes) == plan


def test_the_notes_say_the_plan_was_not_measured() -> None:
    """Anyone reading the row directly sees it too, not only the report."""
    notes = plan_to_notes({"ramp-1": {"target_concurrency": 10}})
    assert "operator-supplied, not measured" in notes


def test_an_absent_plan_reads_as_nothing_declared() -> None:
    assert plan_from_notes(None) == {}
    assert plan_from_notes("artifact sha256: abc") == {}
