"""The capacity table reaching the report.

`derive_calls_per_node` and `capacity_table` were written, tested and never
called. `build_load_payload` accepted a `capacity=` argument that nothing
passed, so the report rendered its empty state no matter how good the data was.
The per-node table is a contractual deliverable, so a correct derivation nobody
invokes is worth exactly nothing.

Two properties are pinned here.

**A refusal travels as its reason, never as a number.** The derivation declines
far more often than it answers, and the reason is the useful half: a report
saying why it could not size the fleet beats one quietly missing the section,
and beats one carrying a figure nobody earned by a much wider margin.

**The ceiling is imported, never restated.** 70% is contractual. A second copy
of it in the CLI would drift from the gates that judge against it.
"""

from __future__ import annotations

import inspect

from voicegateway.cli.loadtest_cli import _capacity_for
from voicegateway.livekit_diag import run_report
from voicegateway.livekit_diag.gates import MAX_NODE_CPU_UTILISATION


def _rows(*triples):
    return [
        {
            "name": f"step-{i}",
            "target_concurrency": t,
            "peak_concurrency": p,
            "peak_cpu_utilisation": c,
        }
        for i, (t, p, c) in enumerate(triples)
    ]


SATURATING = _rows(
    (100, 100, 0.31), (150, 150, 0.48), (200, 200, 0.66), (250, 250, 0.79)
)


# --------------------------------------------------------------------------
# The figure and the table
# --------------------------------------------------------------------------


def test_a_saturating_ramp_yields_a_figure_and_every_tier() -> None:
    capacity = _capacity_for(SATURATING)
    # 200 was sustained at 66%; the 250 step breached the ceiling at 79%.
    assert capacity["calls_per_node"] == 200
    assert [t["target_concurrency"] for t in capacity["tiers"]] == [100, 150, 300, 500]


def test_the_spare_node_is_present_at_every_tier() -> None:
    """A tier sized without it has no node it can afford to lose."""
    for tier in _capacity_for(SATURATING)["tiers"]:
        assert tier["spare_nodes"] == 1
        assert tier["nodes"] == tier["nodes_for_load"] + 1


def test_the_headline_tier_matches_the_formula_by_hand() -> None:
    """500 at C=200: ceil(500 / (0.85 x 200)) + 1 = ceil(2.94) + 1 = 4."""
    tiers = {t["target_concurrency"]: t for t in _capacity_for(SATURATING)["tiers"]}
    assert tiers[500]["usable_per_node"] == 170.0
    assert tiers[500]["nodes_for_load"] == 3
    assert tiers[500]["nodes"] == 4


# --------------------------------------------------------------------------
# Refusals carry their reason and invent nothing
# --------------------------------------------------------------------------


def test_a_refusal_carries_the_reason_and_no_tiers() -> None:
    """The reason is the payload. A refusal must never become a number."""
    plateaued = _rows((100, 100, 0.48), (150, 124, 0.58), (200, 124, 0.58))
    capacity = _capacity_for(plateaued)
    assert capacity["calls_per_node"] is None
    assert capacity["reason"]
    assert "tiers" not in capacity, "tiers were invented for a refused derivation"


def test_the_plateau_value_is_never_returned_as_the_figure() -> None:
    plateaued = _rows((100, 100, 0.48), (150, 124, 0.58), (200, 124, 0.58))
    capacity = _capacity_for(plateaued)
    assert capacity["calls_per_node"] != 124
    assert capacity["calls_per_node"] is None


def test_rows_with_no_cpu_reading_are_refused() -> None:
    """An unscraped ramp shows nothing about a node, whatever it reached."""
    capacity = _capacity_for(_rows((100, 100, None), (200, 200, None)))
    assert capacity["calls_per_node"] is None
    assert "tiers" not in capacity


def test_imported_rows_with_null_targets_do_not_crash() -> None:
    """The real shape. target_concurrency is never imported, so this is the
    common path and it used to raise TypeError."""
    capacity = _capacity_for(_rows((None, 100, 0.31), (None, 150, 0.48)))
    assert capacity["calls_per_node"] is None
    assert "could not be ruled out" in capacity["reason"]


def test_no_instance_type_is_invented() -> None:
    """Nothing here can derive a machine type, so none is supplied."""
    assert "instance_type" not in _capacity_for(SATURATING)


# --------------------------------------------------------------------------
# The threshold is imported, not restated
# --------------------------------------------------------------------------


def test_the_cpu_ceiling_is_the_gate_constant() -> None:
    source = inspect.getsource(_capacity_for)
    assert "MAX_NODE_CPU_UTILISATION" in source
    for literal in ("0.70", "0.7,", "70%"):
        assert literal not in source, f"the CLI restates the ceiling as {literal}"


def test_the_derivation_actually_uses_that_ceiling() -> None:
    """Behavioural, not textual: a step just under it counts, just over does not."""
    under = _capacity_for(_rows((100, 100, MAX_NODE_CPU_UTILISATION - 0.01)))
    over = _capacity_for(_rows((100, 100, MAX_NODE_CPU_UTILISATION + 0.01)))
    # Under the ceiling on every step means the node was never saturated, so it
    # is a floor rather than a measure; over it on every step means nothing was
    # sustained within it. Different refusals, which is the point.
    assert under["reason"] != over["reason"]


# --------------------------------------------------------------------------
# The two empty states say different things
# --------------------------------------------------------------------------


def test_no_capacity_block_is_not_blamed_on_the_run() -> None:
    """Nothing called the derivation. That is a gap in the caller, not the data."""
    html = run_report.render_load_html(
        run_report.build_load_payload(run={"id": "r"}, tests=[])
    )
    assert "none was attempted" in html
    # The gap is in the caller, so the text must not characterise the run's
    # data. Asserted as "says nothing about the run" rather than by quoting the
    # sentence, so a rewording does not fail a test about meaning.
    assert "statement about the run" in html
    assert "not derivable" not in html


def test_a_refused_derivation_reads_differently_and_shows_why() -> None:
    payload = run_report.build_load_payload(
        run={"id": "r"},
        tests=[],
        capacity={"calls_per_node": None, "reason": "the ramp plateaued at 124"},
    )
    html = run_report.render_load_html(payload)
    assert "was not derivable" in html
    assert "the ramp plateaued at 124" in html
    assert "none was attempted" not in html


def test_a_derived_table_renders_its_tiers() -> None:
    payload = run_report.build_load_payload(
        run={"id": "r"}, tests=[], capacity=_capacity_for(SATURATING)
    )
    html = run_report.render_load_html(payload)
    assert "Sized from 200 calls per node" in html
    assert "was not derivable" not in html
