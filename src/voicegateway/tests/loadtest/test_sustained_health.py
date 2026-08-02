"""Sustained Redis and health-check failures, the two criteria with no coverage.

"No sustained Redis or health-check failures" produced no gate at all on a real
run: not a pass, not a fail, not even an UNKNOWN. A report that silently omits an
agreed criterion is worse than one that fails it.

**SUSTAINED, not any.** A single missed sample during a container restart is not
an outage, and grading it as one makes the gate cry wolf until somebody stops
reading it. The bar is a run of consecutive failed samples, named at
:data:`gates.MAX_CONSECUTIVE_FAILED_SAMPLES`, and this file proves the boundary
in both directions rather than trusting it.

**NOT CONFIGURED IS NOT A PASS**, and it is not silence either. An unconfigured
dependency is UNKNOWN with a reason that says which of the two is missing, and
that reason distinguishes "nothing is wired" from "it is wired and failing".
Those are the same status with very different remedies.
"""

from __future__ import annotations

import pytest

from voicegateway.livekit_diag import gates
from voicegateway.loadtest import judge

THRESHOLD = gates.MAX_CONSECUTIVE_FAILED_SAMPLES
HEALTHY = {"name": "ramp-25", "attempted_calls": 150, "succeeded_calls": 150}


def _reading(*samples: int | None, subject: str = gates.HEALTH_SUBJECT_REDIS, **kw):
    return gates.HealthSeriesReading(
        node="sip-1", source="redis-exporter", subject=subject, samples=samples, **kw
    )


# --------------------------------------------------------------------------
# The threshold, proven at its boundary
# --------------------------------------------------------------------------


def test_the_threshold_is_three_and_is_named() -> None:
    """Cited by the report and by the engagement, so it is a named constant."""
    assert THRESHOLD == 3


def test_one_below_the_threshold_does_not_fail() -> None:
    """Two consecutive failures is a blip, not an outage."""
    gate = gates.sustained_health_gate(_reading(1, 0, 0, 1, 1))
    assert gate.status != gates.FAIL
    assert gate.status == gates.WARN


def test_exactly_the_threshold_fails() -> None:
    """Three in a row is the bar, and the bar is inclusive."""
    gate = gates.sustained_health_gate(_reading(1, 0, 0, 0, 1))
    assert gate.status == gates.FAIL
    assert gate.value == 3.0
    assert gate.threshold == float(THRESHOLD)


def test_scattered_failures_warn_rather_than_fail() -> None:
    """Cumulative is not consecutive, and the difference is the criterion.

    Six failures that never run together are a flapping dependency: a real
    finding, and not the criterion being breached.
    """
    gate = gates.sustained_health_gate(_reading(0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0))
    assert gate.status == gates.WARN
    assert gate.value == 1.0


def test_a_clean_window_passes() -> None:
    gate = gates.sustained_health_gate(_reading(1, 1, 1, 1))
    assert gate.status == gates.PASS
    assert gate.value == 0.0


# --------------------------------------------------------------------------
# An unsampled tick is not a failure, and does not bridge two
# --------------------------------------------------------------------------


def test_a_gap_breaks_a_run_rather_than_extending_it() -> None:
    """The trap. Two failures either side of a gap are not three in a row.

    Counting the gap would manufacture an outage out of a missed scrape, which
    is the "unmeasured is never zero" rule in its most consequential form: here
    it would turn a clean run into a FAIL in a client's evidence pack.
    """
    gate = gates.sustained_health_gate(_reading(0, 0, None, 0, 0))
    assert gate.status == gates.WARN
    assert gate.value == 2.0


def test_a_gap_inside_a_real_outage_does_not_rescue_it() -> None:
    """Non-vacuous the other way: three real consecutive failures still fail."""
    gate = gates.sustained_health_gate(_reading(None, 0, 0, 0, None))
    assert gate.status == gates.FAIL


def test_a_window_of_nothing_but_gaps_is_unknown_not_pass() -> None:
    gate = gates.sustained_health_gate(_reading(None, None, None))
    assert gate.status == gates.UNKNOWN
    assert "none of them recorded a result" in gate.detail


@pytest.mark.parametrize("samples", [(0, 0, None), (None, 0, 0)])
def test_a_gap_at_either_edge_still_breaks_the_run(samples) -> None:
    assert gates.sustained_health_gate(_reading(*samples)).status == gates.WARN


# --------------------------------------------------------------------------
# Which failure it was
# --------------------------------------------------------------------------


def test_the_row_says_which_status_was_seen() -> None:
    """429 UnderLoad and 503 Unavailable are different problems.

    A node shedding load at 90% CPU fails the criterion, because a node not
    serving callers is not healthy. But the report must not collapse it with a
    node that is refusing outright.
    """
    gate = gates.sustained_health_gate(
        _reading(
            1,
            0,
            0,
            0,
            subject=gates.HEALTH_SUBJECT_ENDPOINT,
            codes=(200, 429, 503, 503),
        )
    )
    assert gate.status == gates.FAIL
    assert "429 x1" in gate.detail
    assert "503 x2" in gate.detail


def test_no_response_at_all_is_named_rather_than_left_blank() -> None:
    """A refusal and a timeout carry no status code, and that is a third fact."""
    gate = gates.sustained_health_gate(
        _reading(
            0, 0, 0, subject=gates.HEALTH_SUBJECT_ENDPOINT, codes=(None, None, None)
        )
    )
    assert "no response x3" in gate.detail


def test_a_passing_status_is_not_listed_as_a_failure() -> None:
    gate = gates.sustained_health_gate(
        _reading(1, 1, subject=gates.HEALTH_SUBJECT_ENDPOINT, codes=(200, 200))
    )
    assert "200" not in gate.detail


# --------------------------------------------------------------------------
# Not configured is not a pass
# --------------------------------------------------------------------------


def test_an_unconfigured_dependency_is_unknown_with_a_reason() -> None:
    gate = gates.sustained_health_gate(
        gates.HealthSeriesReading(
            node="fleet",
            subject=gates.HEALTH_SUBJECT_REDIS,
            unmeasured_reason="no scrape target uses the redis-exporter source",
        )
    )
    assert gate.status == gates.UNKNOWN
    assert gate.status != gates.PASS
    assert "redis-exporter" in gate.detail


def test_a_run_with_nothing_wired_still_reports_both_criteria() -> None:
    """The original defect: a criterion that produces no row at all.

    Once per RUN, not once per step. Emitting these per test put fourteen
    identical rows in a seven-step report, which is the defect rtp_ports and
    network already had fixed.
    """
    subjects = [
        g.subject
        for g in judge.judge_run([HEALTHY, dict(HEALTHY, name="ramp-50")])
        if g.gate == gates.SUSTAINED_HEALTH_GATE
    ]
    assert sorted(subjects) == ["fleet/health_endpoint", "fleet/redis"]


def test_a_single_step_does_not_emit_the_unconfigured_row_itself() -> None:
    """Non-vacuous: the row is absent per test, which is why it is not repeated."""
    per_test = [
        g for g in judge.judge_test(HEALTHY) if g.gate == gates.SUSTAINED_HEALTH_GATE
    ]
    assert per_test == []


def test_the_two_criteria_are_reported_separately() -> None:
    """Redis wired and no health endpoint is a real configuration.

    Collapsing them into one row would hide which half is missing.
    """
    assert gates.HEALTH_SUBJECT_REDIS != gates.HEALTH_SUBJECT_ENDPOINT


def test_not_configured_reads_differently_from_configured_and_failing() -> None:
    """Same status, very different remedy, so the details must not match."""
    unconfigured = gates.sustained_health_gate(
        gates.HealthSeriesReading(
            node="fleet",
            subject=gates.HEALTH_SUBJECT_REDIS,
            unmeasured_reason="no scrape target uses the redis-exporter source",
        )
    )
    failing = gates.sustained_health_gate(_reading(0, 0, 0))
    assert unconfigured.status == gates.UNKNOWN
    assert failing.status == gates.FAIL
    assert "not evaluated" not in failing.detail


# --------------------------------------------------------------------------
# Per node, per source
# --------------------------------------------------------------------------


def test_rows_are_not_collapsed_across_nodes() -> None:
    """At Stage 3 there are four nodes and an operator needs to know which."""
    readings = [
        gates.HealthSeriesReading(
            node=node,
            source="redis-exporter",
            subject=gates.HEALTH_SUBJECT_REDIS,
            samples=(0, 0, 0) if node == "sip-2" else (1, 1, 1),
        )
        for node in ("sip-1", "sip-2", "sip-3", "sip-4")
    ]
    results = gates.sustained_health_gates(readings)
    assert len(results) == 4
    [failed] = [r for r in results if r.status == gates.FAIL]
    assert failed.subject == "sip-2/redis-exporter/redis"


def test_the_gate_is_registered_and_is_not_a_ratio() -> None:
    """Its value is a count of samples. Rendering 3 as 300% would misstate it."""
    assert gates.SUSTAINED_HEALTH_GATE in gates.ALL_GATES
    assert gates.SUSTAINED_HEALTH_GATE not in gates.RATIO_GATES
