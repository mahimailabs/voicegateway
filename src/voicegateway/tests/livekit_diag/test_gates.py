"""The collapsed health gates.

Two verdict implementations used to disagree (``service._verdict`` vs
``report.check_json``). Each disagreement is pinned here with the reading that
won, so a future edit that quietly relaxes one shows up as a failing test rather
than as a green CI run on a broken deployment.

The tables share one rule: a gate that measured nothing is UNKNOWN, never PASS,
and claims no metric or value. UNKNOWN still exits non-zero.
"""

from __future__ import annotations

import json

import pytest

from voicegateway.livekit_diag import gates

P, F, W, U = gates.PASS, gates.FAIL, gates.WARN, gates.UNKNOWN
_JUNK_COUNTS = (None, "", "abc", [], {})


def _stats(avg: float, mx: float, trials: int) -> dict:
    """A summarize()-shaped stats block."""
    return {"avg": avg, "p50": avg, "p95": mx, "min": avg, "max": mx, "trials": trials}


def _agent(stats: dict, **kw) -> list[dict]:
    return [{"agent": "a", "stats": stats, **kw}]


def _step(
    clients: int, rtt: float | None, quality: str = "Excellent", samples: object = None
) -> dict:
    """A ramp step. Without ``samples`` it is the pre-count shape archived runs have."""
    step = {"clients": clients, "rtt_ms": rtt, "loss_pct": 0.0, "quality": quality}
    if samples is not None:
        step["samples"] = samples
        step["rtt_stat"] = "mean_of_n" if samples else "not_measured"
    return step


def _timed_out(clients: int, quality: str = "Unknown") -> dict:
    """The step a tier reports when not one ping came back."""
    return _step(clients, 0.0, quality, 0)


def _base(rtt: float | None, samples: object = None, quality: str = "Excellent"):
    """An SFU baseline: a ramp step without the tier."""
    return {k: v for k, v in _step(0, rtt, quality, samples).items() if k != "clients"}


def _ok(result: dict) -> dict:
    return {"ok": True, "result": result}


def _sfu_load(baseline: dict, ramp: list) -> dict:
    return _ok(
        {"baseline": baseline, "ramp": ramp, "target_rtt_ms": 50.0, "resource": None}
    )


# ---------------------------------------------------------------------------
# Severity, verdict and exit code
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "code"), [(P, 0), (W, 1), (U, 1), (F, 1), (gates.WAIVED, 1)]
)
def test_exit_code_is_zero_only_for_pass(status: str, code: int) -> None:
    assert gates.exit_code(status) == code


# fmt: off
_WORST = [
    ([P, W, U], U), ([U, F], F), ([P, P], P),
    # A status from a vocabulary this module does not know is not rounded down.
    (["PROBABLY_FINE"], F),
    # WAIVED sits above PASS and below every measured problem.
    ([P, gates.WAIVED], gates.WAIVED), ([gates.WAIVED, W], W),
    ([gates.WAIVED, U], U), ([gates.WAIVED, F], F),
]
# fmt: on


@pytest.mark.parametrize(("statuses", "worst"), _WORST)
def test_worst_status(statuses: list[str], worst: str) -> None:
    assert gates.worst_status(statuses) == worst


def test_a_run_with_no_gates_is_unknown_not_pass() -> None:
    """Nothing was evaluated, so nothing was demonstrated."""
    assert gates.verdict([]) == U


# ---------------------------------------------------------------------------
# evaluate_checks: the whole-run shape
# ---------------------------------------------------------------------------

# fmt: off
_CHECKS = [
    # summarize's fabricated 0.0 avg over zero trials is not a fast reply.
    ({"latency": _ok({"agents": _agent(_stats(0.0, 0.0, 0))})}, [U]),
    # _verdict never read an sfu_load baseline at all.
    ({"sfu_load": _sfu_load({"rtt_ms": 90.0, "quality": "Poor"}, [])}, [F, U]),
    # A check that errored is FAIL, the stricter reading; one no gate reads is UNKNOWN.
    ({"latency": {"ok": False, "error": "check timed out"}}, [F]),
    ({"telepathy": _ok({})}, [U]),
    ({
        "agents": _ok({"agents": []}),
        "sfu": _ok({"baseline": {"rtt_ms": 90.0, "quality": "Poor"}}),
        "latency": _ok({"agents": _agent(_stats(0.5, 0.6, 2))}),
    }, [P, F, P]),
    # Every ping timed out: on the whole probe, then on the baseline alone.
    ({"sfu_load": _sfu_load(_base(0.0, quality="Unknown"), [_timed_out(2), _timed_out(10)])}, [U, U]),
    ({"sfu_load": _sfu_load(_base(0.0, 0), [_step(2, 11.0, samples=3)])}, [U, P]),
]
# fmt: on


@pytest.mark.parametrize(("checks", "statuses"), _CHECKS)
def test_evaluate_checks(checks: dict, statuses: list[str]) -> None:
    results = gates.evaluate_checks(checks, 1500.0)
    assert [g.status for g in results] == statuses
    assert gates.verdict(results) == gates.worst_status(statuses)
    for gate in results:
        json.loads(json.dumps(gate.as_dict()))


def test_a_failed_check_carries_its_error_and_the_keys_are_a_contract() -> None:
    checks = {"latency": {"ok": False, "error": "check timed out"}}
    [gate] = gates.evaluate_checks(checks, 1500.0)
    assert "check timed out" in gate.detail
    keys = "gate status detail subject metric value threshold"
    assert set(gate.as_dict()) == set(keys.split())


def test_zero_agents_in_rooms_still_passes_the_agents_gate() -> None:
    """An idle registered worker is invisible to list_agents, so no count gate."""
    assert gates.agents_gate({"agents": [], "roster": None}).status == P
    gate = gates.agents_gate({"agents": [], "roster": [{"agent_name": "idle"}]})
    assert gate.status == P
    assert "1 worker(s) on the heartbeat roster" in gate.detail


# ---------------------------------------------------------------------------
# latency_gates: fewer than 10 samples is never called p95
# ---------------------------------------------------------------------------

_TWO = _agent(_stats(1.45, 2.4, 2), samples=[0.5, 2.4])
_TEN = _agent(_stats(0.69, 2.4, 10), samples=[0.5] * 9 + [2.4])
_AVG = "agent_reply_latency_avg_ms"

# fmt: off
_LATENCY = [
    (_agent(_stats(0.0, 0.0, 0), error="no worker joined"), False, U, None),
    ([], False, U, None),
    (_TWO, True, W, "agent_reply_latency_max_of_2_ms"),
    (_TEN, True, W, "agent_reply_latency_p95_ms"),
    # The same input is a WARN under --strict: that is the point of the flag.
    (_TWO, False, P, _AVG),
    (_agent(_stats(2.0, 2.5, 2)), False, W, _AVG),
    # No ``trials``: a positive timing is a reading, 0.0 is the no-samples sentinel.
    (_agent({"avg": 0.8}), False, P, _AVG),
    (_agent({"avg": 0.0}), False, U, None),
]
# fmt: on


@pytest.mark.parametrize(("entries", "strict", "status", "metric"), _LATENCY)
def test_latency_gates(entries: list, strict: bool, status: str, metric) -> None:
    [gate] = gates.latency_gates(entries, 1500.0, strict=strict)
    assert gate.status == status
    assert gate.metric == metric


def test_latency_values_come_from_the_statistic_that_decided() -> None:
    [tail] = gates.latency_gates(_TWO, 1500.0, strict=True)
    assert tail.value == 2400.0
    # compute_percentiles interpolates; the legacy summarize p95 would not.
    [p95] = gates.latency_gates(_TEN, 1500.0, strict=True)
    assert p95.value is not None and 500.0 < p95.value < 2400.0
    [nothing] = gates.latency_gates(_LATENCY[0][0], 1500.0)
    assert "no worker joined" in nothing.detail


# ---------------------------------------------------------------------------
# sfu_quality_gate: Poor/Lost FAIL, and quality and rtt are independent readings
#
# A connection that came up while every ping timed out reports "Excellent"
# beside 0.0ms over 0 samples. Loss is never read: sfu.py hardcodes 0.0.
# ---------------------------------------------------------------------------

# fmt: off
_QUALITY = [
    (_base(90.0, quality="Poor"), F), (_base(90.0, quality="Lost"), F),
    (_base(11.0), P), ({"rtt_ms": 11.0, "loss_pct": 99.0, "quality": "Excellent"}, P),
    # "Unknown" is SfuProbe's absence of a reading, not a good one.
    (None, U), ({"quality": "Unknown"}, U),
    (_base(0.0, 0), U), (_base(11.0, 2), P), (_base(11.0, 1), P), (_base(11.0, "3"), P),
    (_base(0.0, 0, "Poor"), F), (_base(0.0, 0, "Lost"), F), (_base(90.0, 2, "Poor"), F),
    # Legacy baselines without a count: the rtt decides, and 0.0 is not a time.
    (_base(0.0), U), ({"quality": "Excellent"}, U), (_base(None), U),
    *[(_base(11.0) | {"samples": j}, P) for j in _JUNK_COUNTS],
    *[(_base(0.0) | {"samples": j}, U) for j in _JUNK_COUNTS],
]
# fmt: on


@pytest.mark.parametrize(("baseline", "status"), _QUALITY)
def test_sfu_quality_gate(baseline: dict | None, status: str) -> None:
    gate = gates.sfu_quality_gate(baseline)
    assert gate.status == status
    assert gate.value is None
    assert (gate.metric is None) == (status == U)


def test_a_degraded_baseline_never_prints_its_empty_mean_as_a_time() -> None:
    gate = gates.sfu_quality_gate(_base(0.0, 0, "Poor"))
    assert "rtt 0.0ms" not in gate.detail


# ---------------------------------------------------------------------------
# sfu_capacity_gate: find_knee's two opposite Nones, and ramps that measured nothing
# ---------------------------------------------------------------------------


def test_find_knee_returns_the_same_none_for_opposite_ramps() -> None:
    """The ambiguity sfu_capacity_gate closes. Sample counts keep the clean None."""
    from voicegateway.livekit_diag.sfu import RampStep, find_knee

    broken = [RampStep(2, 90.0, 0.0, "Poor"), RampStep(10, 120.0, 0.0, "Poor")]
    clean = [
        RampStep(2, 11.0, 0.0, "Excellent", 2),
        RampStep(10, 14.0, 0.0, "Excellent", 10),
    ]
    assert find_knee(broken, 50.0, 1.0) is None
    assert find_knee(clean, 50.0, 1.0) is None


_POOR = [_step(2, 90.0, "Poor")]

# fmt: off
_CAPACITY = [
    (_POOR + [_step(10, 120.0, "Poor")], 50.0, None, F, 90.0),
    ([_step(2, 11.0), _step(10, 14.0)], 50.0, None, P, 11.0),
    # Finding a knee partway up is the reason to run a ramp.
    ([_step(2, 11.0), _step(10, 14.0), _step(25, 88.0, "Poor")], 50.0, None, P, 11.0),
    # A saturated prober describes this host, so it cannot indict the SFU.
    (_POOR, 50.0, {"saturated": True, "cpu_peak": 99.0}, U, None),
    (_POOR, 50.0, {"saturated": None, "cpu_peak": None}, F, 90.0),
    ([], 50.0, None, U, None), ([_step(2, 11.0)], None, None, U, None),
    ([_timed_out(2), _timed_out(10)], 50.0, None, U, None),
    ([_step(10, 11.0, samples=3)], 50.0, None, P, 11.0),
    ([_step(10, 90.0, samples=3)], 50.0, None, F, 90.0),
    # Poor is an observation; its companion 0.0ms is not.
    ([_timed_out(2, "Poor")], 50.0, None, F, None),
    # Legacy steps without a count: 0.0 or no rtt is not a fast reply.
    ([_step(2, 0.0, "Unknown")], 50.0, None, U, None),
    ([_step(2, 0.0)], 50.0, None, U, None),
    ([{"clients": 2, "quality": "Excellent"}], 50.0, None, U, None),
    ([_step(2, 11.0, samples="3")], 50.0, None, P, 11.0),
    *[([_step(2, 11.0) | {"samples": j}], 50.0, None, P, 11.0) for j in _JUNK_COUNTS],
    *[([_step(2, 0.0, "Unknown") | {"samples": j}], 50.0, None, U, None) for j in _JUNK_COUNTS],
]
# fmt: on


@pytest.mark.parametrize(("ramp", "target", "resource", "status", "value"), _CAPACITY)
def test_sfu_capacity_gate(ramp: list, target, resource, status: str, value) -> None:
    gate = gates.sfu_capacity_gate(ramp, target, resource)
    assert gate.status == status
    assert gate.value == value
    if status == U:
        assert gate.metric is None


def test_a_ramp_that_measured_nothing_says_so_and_keeps_its_budget() -> None:
    gate = gates.sfu_capacity_gate([_timed_out(2), _timed_out(10)], 50.0, None)
    assert "samples 0" in gate.detail
    assert gate.threshold == 50.0


# ---------------------------------------------------------------------------
# WAIVED: a threshold the run was not held to, recorded, never a silent pass
# ---------------------------------------------------------------------------


def test_the_waived_status_is_rendered_downstream() -> None:
    """Prometheus drops unknown statuses; the report must not say "could not evaluate"."""
    from voicegateway.livekit_diag import run_report
    from voicegateway.server.api import metrics as metrics_api

    assert gates.WAIVED in metrics_api._GATE_STATUSES
    assert gates.WAIVED in run_report._VERDICT_MEANING
