"""Three presentation defects in a document a client is paying for.

None changes a number. All three were found by reading the rendered file, which
is the only way any of them shows up: each looks correct in a payload and wrong
on the page.

**A duration a reader cannot check.** Wall time rendered as one decimal of a
minute, so a 62-second step and a 66-second step both read "1.1 min". Anybody
comparing the report against their own timing sees a figure that cannot be
reconciled and has no way to tell rounding from disagreement.

**A sentence running into a fragment.** The capacity refusal ended with a full
stop and then appended the derivation's reason, which begins lowercase. It read
as a typo in the one section that exists to explain an absence.

**A blank cell.** Gates describing the whole fleet carried no subject, printing
an empty cell beside populated ones, which reads as a rendering fault rather
than as a scope.
"""

from __future__ import annotations

import pytest

from voicegateway.livekit_diag import gates, run_report
from voicegateway.livekit_diag.run_report import _duration_cell

# --------------------------------------------------------------------------
# Duration
# --------------------------------------------------------------------------


def test_a_sub_ten_minute_run_is_shown_in_seconds() -> None:
    """The unit a ramp step is configured in."""
    assert "62.0" in _duration_cell(62_005)
    assert "s" in _duration_cell(62_005)
    assert "min" not in _duration_cell(62_005)


def test_two_durations_four_seconds_apart_are_distinguishable() -> None:
    """The defect, stated as what it cost. Both used to read "1.1 min"."""
    assert _duration_cell(62_000) != _duration_cell(66_000)


def test_a_long_run_stays_in_minutes() -> None:
    """An hour in seconds is not readable either."""
    assert "min" in _duration_cell(3_600_000)
    assert "60.0" in _duration_cell(3_600_000)


def test_the_boundary_is_ten_minutes() -> None:
    assert "s" in _duration_cell(599_000) and "min" not in _duration_cell(599_000)
    assert "min" in _duration_cell(600_001)


def test_an_absent_duration_is_not_rendered_as_zero() -> None:
    assert "not measured" in _duration_cell(None)
    assert "0" not in _duration_cell(None)


def test_the_unit_is_not_double_spaced() -> None:
    """_num already inserts a non-breaking space, so a leading one shows twice."""
    for ms in (62_005, 3_600_000):
        assert "&nbsp; " not in _duration_cell(ms)


# --------------------------------------------------------------------------
# The capacity refusal
# --------------------------------------------------------------------------


def _capacity_html(capacity: dict) -> str:
    return run_report.render_load_html(
        run_report.build_load_payload(
            run={"id": "r", "artifact_sha256": "a" * 64},
            tests=[],
            capacity=capacity,
        )
    )


def test_the_refusal_reason_is_not_run_into_the_sentence_before_it() -> None:
    """The reason is a fragment and begins lowercase."""
    html = _capacity_html(
        {"calls_per_node": None, "reason": "no step carried both a peak concurrency"}
    )
    assert "rests on. no step" not in html
    assert "Why:" in html


def test_the_reason_is_still_shown_in_full() -> None:
    """Separating it must not have dropped it. The reason is the whole value."""
    reason = "no step recorded a target concurrency or an arrival rate"
    assert run_report._esc(reason) in _capacity_html(
        {"calls_per_node": None, "reason": reason}
    )


def test_a_refusal_with_no_reason_still_says_something() -> None:
    html = _capacity_html({"calls_per_node": None, "reason": ""})
    assert "not stated" in html


# --------------------------------------------------------------------------
# The blank subject
# --------------------------------------------------------------------------


@pytest.mark.parametrize("gate_fn", [gates.node_cpu_gates, gates.node_memory_gates])
def test_a_fleet_wide_gate_names_its_scope(gate_fn) -> None:
    """No node reported, so the finding is fleet-scoped rather than nameless."""
    [gate] = gate_fn([])
    assert gate.subject == gates.FLEET_SUBJECT
    assert gate.status == gates.UNKNOWN


def test_no_gate_row_leaves_the_subject_blank() -> None:
    """Every row in the table identifies what it is about."""
    results = [
        *gates.node_cpu_gates([]),
        *gates.node_memory_gates([]),
        gates.establishment_gate(
            attempted=1,
            succeeded=1,
            threshold=gates.MIN_ESTABLISHMENT_RATIO,
            subject="ramp-20",
        ),
    ]
    for gate in results:
        assert gate.subject, gate


def test_naming_the_fleet_does_not_claim_a_node_reported() -> None:
    """The subject is scope, not evidence. The detail still says nothing came."""
    [gate] = gates.node_cpu_gates([])
    assert "no node was sampled" in gate.detail
    assert gate.value is None


def test_a_measured_node_still_names_itself() -> None:
    """Non-vacuous: the fleet subject is only for the nothing-reported case."""
    [gate] = gates.node_cpu_gates(
        [
            gates.NodeUtilisationReading(
                node="sfu-1", source="node-exporter", utilisation=0.5, samples=9
            )
        ]
    )
    assert gate.subject == "sfu-1/node-exporter"
