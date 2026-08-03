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
    assert 0 < judge.BASELINE_TOLERANCE < 1


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
