"""Connecting measurements to the acceptance gates.

This module exists because the gates had no production caller. Every threshold
was implemented correctly and honestly, and nothing evaluated any of them: a
load-test report could carry a full set of numbers and no verdict at all.

Two properties are pinned here.

**A criterion nobody measured produces an UNKNOWN gate, not a missing one.** An
absent gate reads as a criterion nobody cared about; an UNKNOWN one says nobody
looked, which is both true and useful.

**UNKNOWN never reads as a pass.** A run that failed to measure something did
not demonstrate it.
"""

from __future__ import annotations

import pytest

from voicegateway.livekit_diag import gates
from voicegateway.loadtest import judge

# Aliased: pytest tries to collect any module-level name starting with
# "Test" as a test class, and warns when it has a constructor.
from voicegateway.loadtest.aggregation import TestAggregate as _Aggregate
from voicegateway.repository.node_correlation_repository import window_of

HEALTHY = {"name": "ramp-500", "attempted_calls": 15000, "succeeded_calls": 14985}


def _aggregate(cpu: float | None, memory: float | None, *, node: str = "sfu-1"):
    return _Aggregate(
        window=window_of(1_785_520_800_000, 1_785_524_400_000),
        peak_cpu_utilisation=cpu,
        peak_memory_utilisation=memory,
        node_samples_in_window=12,
        cpu_readings=[
            gates.NodeUtilisationReading(node=node, utilisation=cpu, samples=12)
        ],
        memory_readings=[
            gates.NodeUtilisationReading(node=node, utilisation=memory, samples=12)
        ],
    )


def _by_gate(results):
    out: dict[str, list[str]] = {}
    for r in results:
        out.setdefault(r.gate, []).append(r.status)
    return out


# --------------------------------------------------------------------------
# Every criterion is judged, including the ones nobody measured
# --------------------------------------------------------------------------


def test_all_five_criteria_are_judged_even_with_no_measurements() -> None:
    """None of them may silently vanish from a report.

    Asserted against judge_run, which is what builds a report's gate set. RTP
    ports and network are emitted once per RUN rather than once per node per
    test: nothing measures either anywhere, so a per-node row said the same
    thing eighteen times on a three-step ramp. They are still gates, because a
    written waiver needs something to attach to.
    """
    results = judge.judge_run([HEALTHY])
    kinds = _by_gate(results)
    assert "call_establishment" in kinds
    assert "node_cpu" in kinds
    assert "node_memory" in kinds
    assert "resource_headroom" in kinds
    resources = {
        r.subject.split("/")[-1] for r in results if r.subject and "/" in r.subject
    }
    assert {"file_descriptors", "rtp_ports", "network"} <= resources


def test_an_unmeasured_window_is_unknown_never_pass() -> None:
    results = judge.judge_test(HEALTHY)
    for result in results:
        if result.gate == "call_establishment":
            continue
        assert result.status == gates.UNKNOWN, f"{result.gate} was {result.status}"
    assert judge.verdict_for(results) == gates.UNKNOWN


def test_the_verdict_is_unknown_even_when_establishment_passes() -> None:
    """The one measured criterion must not carry the whole run.

    99.9% establishment is a genuine pass; it says nothing about CPU, and a
    verdict of PASS here would claim it did.
    """
    results = judge.judge_test(HEALTHY)
    establishment = [r for r in results if r.gate == "call_establishment"]
    assert [r.status for r in establishment] == [gates.PASS]
    assert judge.verdict_for(results) == gates.UNKNOWN


# --------------------------------------------------------------------------
# Measured input is judged against the contracted thresholds
# --------------------------------------------------------------------------


def test_a_healthy_run_passes_cpu_and_memory() -> None:
    results = judge.judge_test(HEALTHY, aggregate=_aggregate(0.68, 0.61))
    kinds = _by_gate(results)
    assert kinds["node_cpu"] == [gates.PASS]
    assert kinds["node_memory"] == [gates.PASS]
    assert kinds["call_establishment"] == [gates.PASS]
    # Headroom is still unmeasured, so the RUN is still not a pass.
    assert judge.verdict_for(results) == gates.UNKNOWN


@pytest.mark.parametrize(
    ("cpu", "memory", "expect_cpu", "expect_memory"),
    [
        (0.6999, 0.7499, gates.PASS, gates.PASS),
        (0.70, 0.61, gates.FAIL, gates.PASS),  # strict ceiling
        (0.61, 0.75, gates.PASS, gates.FAIL),  # strict ceiling
        (0.95, 0.99, gates.FAIL, gates.FAIL),
    ],
)
def test_the_ceilings_are_strict(cpu, memory, expect_cpu, expect_memory) -> None:
    """Sitting exactly on the ceiling is not under it."""
    kinds = _by_gate(judge.judge_test(HEALTHY, aggregate=_aggregate(cpu, memory)))
    assert kinds["node_cpu"] == [expect_cpu]
    assert kinds["node_memory"] == [expect_memory]


def test_a_breached_ceiling_fails_the_run() -> None:
    results = judge.judge_test(HEALTHY, aggregate=_aggregate(0.91, 0.61))
    assert judge.verdict_for(results) == gates.FAIL


def test_a_run_below_the_establishment_floor_fails() -> None:
    poor = {"name": "ramp", "attempted_calls": 10000, "succeeded_calls": 9940}
    results = judge.judge_test(poor, aggregate=_aggregate(0.5, 0.5))
    assert _by_gate(results)["call_establishment"] == [gates.FAIL]
    assert judge.verdict_for(results) == gates.FAIL


def test_unimported_counts_are_unknown_not_zero() -> None:
    """A test whose counts never arrived did not establish 0% of its calls."""
    blank = {"name": "ramp"}
    results = judge.judge_test(blank, aggregate=_aggregate(0.5, 0.5))
    assert _by_gate(results)["call_establishment"] == [gates.UNKNOWN]


# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------


def test_a_run_judges_every_test() -> None:
    tests = [
        {"name": "a", "attempted_calls": 100, "succeeded_calls": 100},
        {"name": "b", "attempted_calls": 100, "succeeded_calls": 50},
    ]
    results = judge.judge_run(
        tests, aggregates={"a": _aggregate(0.5, 0.5), "b": _aggregate(0.5, 0.5)}
    )
    subjects = {r.subject for r in results if r.gate == "call_establishment"}
    assert subjects == {"a", "b"}
    # One test below the floor fails the run.
    assert judge.verdict_for(results) == gates.FAIL


def test_a_test_with_no_aggregate_is_judged_as_uncorrelated() -> None:
    """Missing from the mapping means nothing correlated to it, not skip it."""
    tests = [{"name": "a", "attempted_calls": 100, "succeeded_calls": 100}]
    results = judge.judge_run(tests, aggregates={})
    assert _by_gate(results)["node_cpu"] == [gates.UNKNOWN]
    assert judge.verdict_for(results) == gates.UNKNOWN


def test_nothing_here_restates_a_threshold() -> None:
    """The numbers live in gates.py. A second copy is a second thing to drift."""
    import inspect

    source = inspect.getsource(judge)
    for literal in ("0.995", "0.70", "0.75", "0.20", "0.85"):
        assert literal not in source, f"judge.py restates the threshold {literal}"
