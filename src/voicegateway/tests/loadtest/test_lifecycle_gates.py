"""The three contracted criteria that had no gate at all.

The report emitted five gate families, and mapping them onto the client's list
left three lines uncovered:

* "No crashes, OOM events, port exhaustion, file-descriptor exhaustion, or
  unplanned restarts." Only file descriptors were gated.
* "No increasing stale calls, rooms, sessions, or memory usage." Nothing looked
  at trend, so a run that leaked steadily for ten minutes passed every gate.
* "Resource usage returns close to baseline after calls terminate." Nothing
  compared before against after.

All three are computable from columns ``node_samples`` already stored, except
the kernel OOM counter, which was verified present on a live node-exporter
before being wired rather than assumed.

**Restarts and OOM are one criterion and two signals, never one.** A crash
presents as a restart, so the restart check covers crashes. It does NOT cover
OOM: the kernel can kill a child or a sibling without the scraped service
restarting, so a clean restart signal must never vouch for the OOM half.
"""

from __future__ import annotations

import pytest

from voicegateway.livekit_diag import gates
from voicegateway.loadtest import judge

MIN = gates.MIN_TREND_SAMPLES


def _life(**kw):
    return gates.LifecycleReading(node="sip-1", source="livekit-sip", **kw)


def _trend(values, *, rising_is_bad=True, metric="sip_calls_active"):
    return gates.TrendReading(
        node="sip-1",
        source="livekit-sip",
        metric=metric,
        values=tuple(values),
        rising_is_bad=rising_is_bad,
    )


# --------------------------------------------------------------------------
# Restarts, crashes and OOM
# --------------------------------------------------------------------------


def test_a_rising_start_time_is_a_restart() -> None:
    """process_start_time_seconds is constant for a process's life."""
    gate = gates.process_lifecycle_gate(
        _life(start_times=(1785.0, 1785.0, 1900.0), oom_kills=(0.0, 0.0, 0.0))
    )
    assert gate.status == gates.FAIL
    assert "restarted" in gate.detail


def test_a_steady_start_time_across_the_window_passes() -> None:
    gate = gates.process_lifecycle_gate(
        _life(start_times=(1785.0,) * 5, oom_kills=(0.0,) * 5)
    )
    assert gate.status == gates.PASS


def test_an_oom_kill_fails_even_without_a_restart() -> None:
    """The whole reason these are two signals.

    The kernel killed something and the scraped service never restarted, so the
    restart check is clean and the criterion is still breached.
    """
    gate = gates.process_lifecycle_gate(
        _life(start_times=(1785.0,) * 3, oom_kills=(0.0, 0.0, 1.0))
    )
    assert gate.status == gates.FAIL
    assert "OOM" in gate.detail
    assert "did not restart" in gate.detail


def test_an_oom_counter_high_but_flat_is_not_this_runs_finding() -> None:
    """Cumulative since boot. A kill last week is not a kill during the run."""
    gate = gates.process_lifecycle_gate(
        _life(start_times=(1785.0,) * 3, oom_kills=(7.0, 7.0, 7.0))
    )
    assert gate.status == gates.PASS


def test_no_restart_does_not_vouch_for_oom() -> None:
    """The trap, stated as a test. An unmeasured half is UNKNOWN, not covered."""
    gate = gates.process_lifecycle_gate(_life(start_times=(1785.0,) * 4))
    assert gate.status == gates.UNKNOWN
    assert "does not prove no OOM" in gate.detail


def test_no_oom_does_not_vouch_for_restarts() -> None:
    gate = gates.process_lifecycle_gate(_life(oom_kills=(0.0, 0.0)))
    assert gate.status == gates.UNKNOWN
    assert "does not prove no restart" in gate.detail


def test_a_subject_with_nothing_measured_is_unknown_not_pass() -> None:
    gate = gates.process_lifecycle_gate(_life(start_times=(None, None)))
    assert gate.status == gates.UNKNOWN


def test_gaps_do_not_read_as_a_restart() -> None:
    """A NULL is a missed scrape, not a process that started at zero."""
    gate = gates.process_lifecycle_gate(
        _life(start_times=(1785.0, None, 1785.0), oom_kills=(0.0, None, 0.0))
    )
    assert gate.status == gates.PASS


def test_rows_are_per_node_per_source() -> None:
    readings = [
        gates.LifecycleReading(
            node=node,
            source="livekit-sip",
            start_times=(1785.0, 1900.0) if node == "sip-2" else (1785.0, 1785.0),
            oom_kills=(0.0, 0.0),
        )
        for node in ("sip-1", "sip-2")
    ]
    results = gates.process_lifecycle_gates(readings)
    assert len(results) == 2
    [failed] = [r for r in results if r.status == gates.FAIL]
    assert failed.subject == "sip-2/livekit-sip"


# --------------------------------------------------------------------------
# Stale resource trend
# --------------------------------------------------------------------------


def test_a_ramp_is_not_a_leak() -> None:
    """The trap this gate is shaped around.

    First-versus-last would read every successful ramp as a leak. Middle third
    against final third excludes the ramp by construction.
    """
    ramp = [1, 5, 10] + [20] * MIN + [20] * MIN
    assert gates.resource_trend_gate(_trend(ramp)).status == gates.PASS


def test_a_steady_climb_after_the_ramp_is_a_leak() -> None:
    values = [1, 5, 10] + [20] * MIN + [30] * MIN
    gate = gates.resource_trend_gate(_trend(values))
    assert gate.status == gates.FAIL
    assert gate.value == pytest.approx(10.0)


def test_free_memory_falling_is_the_leak_direction() -> None:
    """memory_available_bytes leaks DOWNWARD. Reading it upward would invert."""
    falling = [900] * MIN + [900] * MIN + [500] * MIN
    gate = gates.resource_trend_gate(
        _trend(falling, rising_is_bad=False, metric="memory_available_bytes")
    )
    assert gate.status == gates.FAIL


def test_free_memory_rising_is_not_a_leak() -> None:
    rising = [500] * MIN + [500] * MIN + [900] * MIN
    gate = gates.resource_trend_gate(
        _trend(rising, rising_is_bad=False, metric="memory_available_bytes")
    )
    assert gate.status == gates.PASS


# The fourteen resource_trend rows a real 100-concurrent fleet run produced:
# (node, metric, steady-state baseline, drift, rising_is_bad). Every one of them
# must PASS. Two of them did not before the magnitude floor existed, and one of
# those was the box running the collector, carrying no test load at all.
FLEET_ROWS = (
    ("agent-0", "memory_available_bytes", 3.2e10, 17_452_686, False),
    ("loadgen-0", "memory_available_bytes", 1.57e10, 2_448_071, False),
    ("loadgen-1", "memory_available_bytes", 9.5e9, 9_500, False),
    ("monitor-0", "memory_available_bytes", 7.15e9, -406_727, False),
    ("sfu-1", "memory_available_bytes", 7.14e9, -7_607_979, False),
    ("sfu-2", "memory_available_bytes", 7.25e9, 1_500_302, False),
    ("sip-1", "memory_available_bytes", 1.54e10, 45_882_510, False),
    ("sip-2", "memory_available_bytes", 1.57e10, 6_103_410, False),
    ("sfu-1", "rooms", 57.9, -10, True),
    ("sfu-1", "participants", 215.7, -57, True),
    ("sfu-2", "rooms", 58.9, -19, True),
    ("sfu-2", "participants", 110.4, -38, True),
    ("sip-1", "sip_calls_active", 98.2, -28, True),
    ("sip-2", "sip_calls_active", 100.0, 0, True),
)


def _steady(baseline: float, drift: float):
    """A window that ramps, holds at baseline, then settles at baseline+drift."""
    return tuple([0.0] * MIN + [baseline] * MIN + [baseline + drift] * MIN)


@pytest.mark.parametrize(
    ("node", "metric", "baseline", "drift", "rising_is_bad"), FLEET_ROWS
)
def test_no_row_from_the_real_fleet_run_fails(
    node, metric, baseline, drift, rising_is_bad
) -> None:
    """Regression, against measured data rather than an invented fixture.

    monitor-0 failed on 407 KB, 0.0057% of 7.15 GB, over ten minutes, on the box
    running the collector. sip-1 moved +45.8 MB the other way and passed. The
    gate was reading the SIGN of ordinary memory noise.
    """
    gate = gates.resource_trend_gate(
        gates.TrendReading(
            node=node,
            metric=metric,
            values=_steady(baseline, drift),
            rising_is_bad=rising_is_bad,
            window_ms=600_000,
        )
    )
    assert gate.status == gates.PASS, gate.detail


def test_noise_below_the_floor_is_pass_not_unknown() -> None:
    """The measurement SUCCEEDED and found no meaningful drift.

    UNKNOWN is for the sample-count case, where nothing could be concluded.
    Using it here would report a clean node as unevaluated.
    """
    gate = gates.resource_trend_gate(
        gates.TrendReading(
            node="monitor-0",
            metric="memory_available_bytes",
            values=_steady(7.15e9, -406_727),
            rising_is_bad=False,
        )
    )
    assert gate.status == gates.PASS
    assert gate.status != gates.UNKNOWN
    assert "measurement noise rather than a leak" in gate.detail


def test_a_drift_above_the_floor_still_fails() -> None:
    """Non-vacuous: the floor must not have turned the gate off."""
    gate = gates.resource_trend_gate(
        gates.TrendReading(
            node="sfu-9",
            metric="memory_available_bytes",
            values=_steady(7.14e9, -7.14e9 * 0.05),
            rising_is_bad=False,
        )
    )
    assert gate.status == gates.FAIL


def test_the_floor_sits_well_above_observed_noise() -> None:
    """1% against a largest observed noise of 0.05% and a largest favourable
    movement of 0.3%, both from the fleet run above."""
    assert gates.MIN_TREND_DRIFT_FRACTION == 0.01
    # Only the rows moving the WRONG way have to clear the floor. The count
    # metrics moved 17% to 34% on that run and are nowhere near it, but they
    # were draining, which is the favourable direction and passes on sign.
    unfavourable = [
        abs(d) / b
        for _, _, b, d, rising_bad in FLEET_ROWS
        if b and (d > 0 if rising_bad else d < 0)
    ]
    assert unfavourable, "fixture would be vacuous with nothing moving wrongly"
    assert max(unfavourable) < gates.MIN_TREND_DRIFT_FRACTION


def test_the_boundary_is_inclusive() -> None:
    """Exactly at the floor is a leak, so the bar is a bar and not a gap."""
    base = 1000.0
    at = gates.resource_trend_gate(
        gates.TrendReading(node="n", metric="m", values=_steady(base, 10.0))
    )
    below = gates.resource_trend_gate(
        gates.TrendReading(node="n", metric="m", values=_steady(base, 9.0))
    )
    assert at.status == gates.FAIL
    assert below.status == gates.PASS


def test_a_zero_baseline_counts_whole_units() -> None:
    """No fraction exists to take. A count going 0 to 5 in steady state is real."""
    gate = gates.resource_trend_gate(
        gates.TrendReading(node="n", metric="rooms", values=_steady(0.0, 5.0))
    )
    assert gate.status == gates.FAIL


def test_the_rate_is_reported_but_never_gated_on() -> None:
    """A soak and a ten-minute run are not comparable by absolute drift.

    The rate is what makes them comparable to a reader. It is deliberately NOT
    the threshold: noise scales with the size of the thing measured, not with
    how long you watch, so a rate threshold would divide a fixed noise floor by
    a short window and make short runs hypersensitive.
    """
    values = _steady(1000.0, 5.0)
    with_window = gates.resource_trend_gate(
        gates.TrendReading(node="n", metric="m", values=values, window_ms=600_000)
    )
    without = gates.resource_trend_gate(
        gates.TrendReading(node="n", metric="m", values=values)
    )
    assert "per hour" in with_window.detail
    assert "per hour" not in without.detail
    assert with_window.status == without.status


def test_too_few_samples_is_unknown_not_pass() -> None:
    """ "Too short to tell" and "flat" look identical in one number."""
    gate = gates.resource_trend_gate(_trend([1, 1, 1, 1, 1, 1]))
    assert gate.status == gates.UNKNOWN
    assert "Too short to tell is not flat" in gate.detail


def test_gaps_are_skipped_rather_than_read_as_zero() -> None:
    """A gap must not drag a mean down as if it were a zero.

    Sized so each third still clears MIN_TREND_SAMPLES after its gap is
    removed, because a third that thins below the floor is correctly UNKNOWN
    and would prove nothing about how gaps are valued.
    """
    values = [1, 2, 3, 4] + [10, None, 10, 10] + [10, None, 10, 10]
    assert gates.resource_trend_gate(_trend(values)).status == gates.PASS


def test_the_thirds_split_excludes_the_first_third() -> None:
    middle, final = gates.steady_state_thirds([0, 0, 0, 1, 1, 1, 2, 2, 2])
    assert middle == [1, 1, 1]
    assert final == [2, 2, 2]


# --------------------------------------------------------------------------
# Return to baseline
# --------------------------------------------------------------------------


def test_no_pre_run_baseline_is_unknown_not_pass() -> None:
    """Nobody established what baseline was, so nothing returned to it."""
    [gate] = gates.return_to_baseline_gates(
        [
            gates.BaselineComparison(
                node="sip-1",
                metric="memory_available_bytes",
                baseline=None,
                post_settle=1000.0,
                unmeasured_reason=(
                    "no idle sample was recorded before the test, so no "
                    "baseline was ever established to return to"
                ),
            )
        ],
        tolerance=judge.BASELINE_TOLERANCE,
    )
    assert gate.status == gates.UNKNOWN
    assert gate.status != gates.PASS


def test_the_tolerance_is_stated_by_the_caller_not_defaulted() -> None:
    """return_to_baseline_gates refuses to invent "near". The judge states it."""
    with pytest.raises(TypeError):
        gates.return_to_baseline_gates([])  # type: ignore[call-arg]
    # A ratio CEILING, so it sits above 1.0: the resource may end a little
    # higher than baseline. Below 1.0 would demand the resource shrink, which
    # fails a node that ended exactly where it started.
    assert judge.BASELINE_TOLERANCE > 1.0


# --------------------------------------------------------------------------
# All three reach a run, and none of them is a ratio
# --------------------------------------------------------------------------


def test_a_run_with_no_window_still_reports_all_three() -> None:
    """The original defect: a contracted criterion with no row at all.

    Once per RUN, not per step. judge_test deliberately emits none of these on
    its own, so a seven-step run says each absence once instead of seven times.
    """
    families = {
        g.gate
        for g in judge.judge_run(
            [{"name": "r", "attempted_calls": 1, "succeeded_calls": 1}]
        )
    }
    assert gates.PROCESS_LIFECYCLE_GATE in families
    assert gates.RESOURCE_TREND_GATE in families
    assert gates.RETURN_TO_BASELINE_GATE in families


@pytest.mark.parametrize(
    "gate", [gates.PROCESS_LIFECYCLE_GATE, gates.RESOURCE_TREND_GATE]
)
def test_count_gates_are_registered_and_are_not_ratios(gate) -> None:
    """A count rendered as a percentage is what RATIO_GATES exists to prevent."""
    assert gate in gates.ALL_GATES
    assert gate not in gates.RATIO_GATES
