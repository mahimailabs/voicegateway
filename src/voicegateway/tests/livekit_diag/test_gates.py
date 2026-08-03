"""The collapsed health gates.

Two verdict implementations used to disagree (``service._verdict`` vs
``report.check_json``). Each disagreement is pinned here with the reading that
won, so a future edit that quietly relaxes one shows up as a failing test rather
than as a green CI run on a broken deployment.
"""

from __future__ import annotations

import pytest

from voicegateway.livekit_diag import gates


def _stats(avg: float, mx: float, trials: int) -> dict:
    """A summarize()-shaped stats block."""
    return {"avg": avg, "p50": avg, "p95": mx, "min": avg, "max": mx, "trials": trials}


# ---------------------------------------------------------------------------
# Severity, verdict and exit code
# ---------------------------------------------------------------------------


def test_exit_code_is_zero_only_for_pass():
    assert gates.exit_code(gates.PASS) == 0
    assert gates.exit_code(gates.WARN) == 1
    assert gates.exit_code(gates.UNKNOWN) == 1
    assert gates.exit_code(gates.FAIL) == 1


def test_a_run_with_no_gates_is_unknown_not_pass():
    """Nothing was evaluated, so nothing was demonstrated."""
    assert gates.verdict([]) == gates.UNKNOWN


def test_unknown_outranks_warn_and_fail_outranks_unknown():
    assert gates.worst_status([gates.PASS, gates.WARN, gates.UNKNOWN]) == gates.UNKNOWN
    assert gates.worst_status([gates.UNKNOWN, gates.FAIL]) == gates.FAIL
    assert gates.worst_status([gates.PASS, gates.PASS]) == gates.PASS
    # A status from a vocabulary this module does not know is not rounded down.
    assert gates.worst_status(["PROBABLY_FINE"]) == gates.FAIL


# ---------------------------------------------------------------------------
# Disagreement 1: a probe with zero successful trials
#   _verdict: PASS (summarize's fabricated 0.0 avg is under any target)
#   check_json: WARN
#   winner: not-healthy -- and specifically UNKNOWN, since nothing was measured
# ---------------------------------------------------------------------------


def test_a_probe_that_measured_nothing_does_not_pass():
    entries = [
        {"agent": "a", "stats": _stats(0.0, 0.0, 0), "error": "no worker joined"}
    ]
    [gate] = gates.latency_gates(entries, 1500.0)
    assert gate.status == gates.UNKNOWN
    assert "no successful probe" in gate.detail
    assert "no worker joined" in gate.detail
    # Nothing decided it, so no metric is claimed.
    assert gate.metric is None
    assert gates.exit_code(gates.verdict([gate])) == 1


def test_zero_trials_is_not_rescued_by_the_fabricated_zero_average():
    """The exact input the old dashboard verdict called healthy."""
    checks = {
        "latency": {
            "ok": True,
            "result": {"agents": [{"agent": "a", "stats": _stats(0.0, 0.0, 0)}]},
        }
    }
    assert gates.verdict(gates.evaluate_checks(checks, 1500.0)) == gates.UNKNOWN


# ---------------------------------------------------------------------------
# Disagreement 2: no latency result at all (no agent was in a room)
#   both implementations: PASS (the loop had nothing to iterate)
#   winner: UNKNOWN -- a gate that did not run has not passed
# ---------------------------------------------------------------------------


def test_no_agent_probed_is_unknown_not_pass():
    [gate] = gates.latency_gates([], 1500.0)
    assert gate.status == gates.UNKNOWN
    assert "no agent was probed" in gate.detail


# ---------------------------------------------------------------------------
# Disagreement 3: SFU baseline quality Poor / Lost
#   _verdict: WARN
#   check_json: FAIL
#   winner: FAIL
# ---------------------------------------------------------------------------


def test_degraded_sfu_quality_fails():
    for quality in ("Poor", "Lost"):
        gate = gates.sfu_quality_gate(
            {"rtt_ms": 90.0, "loss_pct": 0.0, "quality": quality}
        )
        assert gate.status == gates.FAIL, quality


def test_healthy_sfu_quality_passes():
    gate = gates.sfu_quality_gate(
        {"rtt_ms": 11.0, "loss_pct": 0.0, "quality": "Excellent"}
    )
    assert gate.status == gates.PASS


def test_missing_or_unreadable_sfu_quality_is_unknown():
    assert gates.sfu_quality_gate(None).status == gates.UNKNOWN
    # SfuProbe reports "Unknown" when no client stayed connected: that is the
    # absence of a reading, not a good one.
    assert gates.sfu_quality_gate({"quality": "Unknown"}).status == gates.UNKNOWN


def test_loss_is_never_gated_because_it_is_a_hardcoded_constant():
    """``sfu.py`` hardcodes ``loss_pct = 0.0``; gating on it would be theatre.

    Both old implementations carried a ``loss_pct > 1.0`` branch that could
    never fire. A baseline that somehow reported catastrophic loss alongside a
    healthy quality must still pass, because this gate does not read that field
    at all -- if it ever did, the number it read would be fabricated.
    """
    gate = gates.sfu_quality_gate(
        {"rtt_ms": 11.0, "loss_pct": 99.0, "quality": "Excellent"}
    )
    assert gate.status == gates.PASS


# ---------------------------------------------------------------------------
# Disagreement 4: an sfu_load baseline was never read by _verdict at all
# ---------------------------------------------------------------------------


def test_sfu_load_baseline_is_gated_too():
    checks = {
        "sfu_load": {
            "ok": True,
            "result": {
                "baseline": {"rtt_ms": 90.0, "loss_pct": 0.0, "quality": "Poor"},
                "ramp": [],
                "target_rtt_ms": 50.0,
                "resource": None,
            },
        }
    }
    assert gates.verdict(gates.evaluate_checks(checks, 1500.0)) == gates.FAIL


# ---------------------------------------------------------------------------
# Disagreement 5: a check that errored or timed out
#   _verdict: FAIL. check_json never saw one. FAIL is the stricter reading.
# ---------------------------------------------------------------------------


def test_a_failed_check_is_a_fail_not_an_unknown():
    checks = {"latency": {"ok": False, "error": "check timed out"}}
    [gate] = gates.evaluate_checks(checks, 1500.0)
    assert gate.status == gates.FAIL
    assert "check timed out" in gate.detail


def test_an_unrecognised_check_is_unknown():
    checks = {"telepathy": {"ok": True, "result": {}}}
    [gate] = gates.evaluate_checks(checks, 1500.0)
    assert gate.status == gates.UNKNOWN


# ---------------------------------------------------------------------------
# The agents gate asserts the API answered, never that agents exist
# ---------------------------------------------------------------------------


def test_zero_agents_in_rooms_still_passes_the_agents_gate():
    """An idle registered worker is invisible to list_agents.

    A count gate here would fail every healthy fleet that happens to be between
    calls, which is why this gate only claims the listing succeeded.
    """
    gate = gates.agents_gate({"agents": [], "roster": None})
    assert gate.status == gates.PASS
    assert "0 agent(s)" in gate.detail


def test_agents_gate_mentions_the_roster_when_there_is_one():
    gate = gates.agents_gate({"agents": [], "roster": [{"agent_name": "idle"}]})
    assert "1 worker(s) on the heartbeat roster" in gate.detail


# ---------------------------------------------------------------------------
# Percentile honesty: fewer than 10 samples is never called p95
# ---------------------------------------------------------------------------


def test_strict_names_the_tail_max_of_n_below_ten_samples():
    entries = [
        {"agent": "a", "stats": _stats(1.45, 2.4, 2), "samples": [0.5, 2.4]},
    ]
    [gate] = gates.latency_gates(entries, 1500.0, strict=True)
    assert gate.metric == "agent_reply_latency_max_of_2_ms"
    assert "p95" not in gate.metric
    assert gate.value == 2400.0
    assert gate.status == gates.WARN


def test_strict_names_p95_only_from_ten_samples_up():
    samples = [0.5] * 9 + [2.4]
    entries = [{"agent": "a", "stats": _stats(0.69, 2.4, 10), "samples": samples}]
    [gate] = gates.latency_gates(entries, 1500.0, strict=True)
    assert gate.metric == "agent_reply_latency_p95_ms"
    # compute_percentiles interpolates; the legacy summarize p95 would not.
    assert gate.value is not None and 500.0 < gate.value < 2400.0


def test_default_mode_gates_the_average_and_names_it():
    entries = [{"agent": "a", "stats": _stats(1.45, 2.4, 2), "samples": [0.5, 2.4]}]
    [gate] = gates.latency_gates(entries, 1500.0)
    assert gate.metric == "agent_reply_latency_avg_ms"
    assert gate.status == gates.PASS
    # The same input is a WARN under --strict: that is the whole point of the flag.
    [strict_gate] = gates.latency_gates(entries, 1500.0, strict=True)
    assert strict_gate.status == gates.WARN


def test_a_slow_average_warns():
    entries = [{"agent": "a", "stats": _stats(2.0, 2.5, 2)}]
    [gate] = gates.latency_gates(entries, 1500.0)
    assert gate.status == gates.WARN
    assert "over the 1500ms target" in gate.detail


def test_a_payload_without_a_trial_count_but_with_a_timing_is_evaluated():
    """A stats block that omits ``trials`` is judged on whether it has a number.

    ``summarize`` always sets ``trials``, but a caller-supplied payload may not.
    A positive timing is a real measurement; a 0.0 is summarize's "no samples"
    sentinel and must not be read as an instant reply.
    """
    [ok] = gates.latency_gates([{"agent": "a", "stats": {"avg": 0.8}}], 1500.0)
    assert ok.status == gates.PASS
    [nothing] = gates.latency_gates([{"agent": "a", "stats": {"avg": 0.0}}], 1500.0)
    assert nothing.status == gates.UNKNOWN


# ---------------------------------------------------------------------------
# find_knee's two opposite Nones
# ---------------------------------------------------------------------------


def _step(clients: int, rtt: float, quality: str = "Excellent") -> dict:
    return {"clients": clients, "rtt_ms": rtt, "loss_pct": 0.0, "quality": quality}


def test_a_ramp_that_broke_at_the_first_tier_fails():
    """``find_knee`` returns None here AND for a clean ramp. Only one is healthy."""
    from voicegateway.livekit_diag.sfu import RampStep, find_knee

    steps = [RampStep(2, 90.0, 0.0, "Poor"), RampStep(10, 120.0, 0.0, "Poor")]
    assert find_knee(steps, 50.0, 1.0) is None  # the ambiguity this gate closes

    gate = gates.sfu_capacity_gate(
        [_step(2, 90.0, "Poor"), _step(10, 120.0, "Poor")], 50.0, None
    )
    assert gate.status == gates.FAIL
    assert "no healthy capacity" in gate.detail


def test_a_ramp_that_never_broke_passes():
    from voicegateway.livekit_diag.sfu import RampStep, find_knee

    # The sample counts keep this None meaning "every tier was measured and
    # stayed in budget". Drop them and the walk stops at tier one for lack of
    # evidence, which is the same None for the opposite reason.
    steps = [
        RampStep(2, 11.0, 0.0, "Excellent", 2),
        RampStep(10, 14.0, 0.0, "Excellent", 10),
    ]
    assert find_knee(steps, 50.0, 1.0) is None  # same None, opposite outcome

    gate = gates.sfu_capacity_gate([_step(2, 11.0), _step(10, 14.0)], 50.0, None)
    assert gate.status == gates.PASS


def test_finding_a_knee_partway_up_the_ramp_is_not_a_failure():
    """Measuring where capacity ends is the reason to run a ramp."""
    ramp = [_step(2, 11.0), _step(10, 14.0), _step(25, 88.0, "Poor")]
    assert gates.sfu_capacity_gate(ramp, 50.0, None).status == gates.PASS


def test_a_saturated_prober_makes_capacity_unknown_not_failed():
    """The curve then describes this host, so it cannot indict the SFU."""
    ramp = [_step(2, 90.0, "Poor")]
    resource = {"saturated": True, "cpu_peak": 99.0}
    gate = gates.sfu_capacity_gate(ramp, 50.0, resource)
    assert gate.status == gates.UNKNOWN
    assert "prober host saturated" in gate.detail


def test_an_unmeasured_prober_still_evaluates_but_says_so():
    ramp = [_step(2, 90.0, "Poor")]
    resource = {"saturated": None, "cpu_peak": None}
    gate = gates.sfu_capacity_gate(ramp, 50.0, resource)
    assert gate.status == gates.FAIL
    assert "was not measured" in gate.detail


def test_a_ramp_with_no_steps_or_no_threshold_is_unknown():
    assert gates.sfu_capacity_gate([], 50.0, None).status == gates.UNKNOWN
    assert gates.sfu_capacity_gate([_step(2, 11.0)], None, None).status == gates.UNKNOWN


# ---------------------------------------------------------------------------
# A ramp that measured nothing: 0.0ms is under every budget
# ---------------------------------------------------------------------------


def _measured(
    clients: int, rtt: float, samples: int, quality: str = "Excellent"
) -> dict:
    """A step as ``service.sfu`` publishes it, with its sample count."""
    return {
        "clients": clients,
        "rtt_ms": rtt,
        "loss_pct": 0.0,
        "quality": quality,
        "samples": samples,
        "rtt_stat": "mean_of_n" if samples else "not_measured",
    }


def _timed_out(clients: int, quality: str = "Unknown") -> dict:
    """The step a tier reports when not one ping came back."""
    return _measured(clients, 0.0, 0, quality)


def test_a_ramp_where_every_ping_timed_out_does_not_pass():
    """The mirror image of a healthy server reading FAIL, and worse.

    ``0.0 > target`` is False and ``Unknown`` is not in _DEGRADED_QUALITY, so
    this ramp used to satisfy the gate and report PASS: a monitoring tool
    telling an operator that a server it could not reach at all is fine.
    """
    ramp = [_timed_out(2), _timed_out(10)]
    gate = gates.sfu_capacity_gate(ramp, 50.0, None)
    assert gate.status == gates.UNKNOWN
    assert gate.status != gates.PASS
    assert "measured nothing" in gate.detail
    assert "samples 0" in gate.detail
    # Nothing decided it, so no number is published: a value of 0.0 here is the
    # placeholder that caused the bug, not a reading.
    assert gate.metric is None and gate.value is None
    assert gate.threshold == 50.0
    # UNKNOWN is not the soft option: the run still exits non-zero.
    assert gates.exit_code(gates.verdict([gate])) == 1


def test_an_unmeasured_ramp_is_unknown_through_the_whole_run():
    checks = {
        "sfu_load": {
            "ok": True,
            "result": {
                "baseline": {"rtt_ms": 0.0, "loss_pct": 0.0, "quality": "Unknown"},
                "ramp": [_timed_out(2), _timed_out(10)],
                "target_rtt_ms": 50.0,
                "resource": None,
            },
        }
    }
    results = gates.evaluate_checks(checks, 1500.0)
    capacity = [g for g in results if g.gate == gates.SFU_CAPACITY_GATE]
    assert [g.status for g in capacity] == [gates.UNKNOWN]
    assert gates.verdict(results) != gates.PASS

    import json

    for gate in results:
        json.loads(json.dumps(gate.as_dict()))


def test_a_partially_measured_tier_is_judged_on_what_it_measured():
    """Some pongs back is a measurement; the budget for it is unchanged."""
    healthy = gates.sfu_capacity_gate([_measured(10, 11.0, 3)], 50.0, None)
    assert healthy.status == gates.PASS
    assert healthy.value == 11.0

    slow = gates.sfu_capacity_gate([_measured(10, 90.0, 3)], 50.0, None)
    assert slow.status == gates.FAIL
    assert slow.value == 90.0


def test_a_degraded_tier_that_measured_no_rtt_is_still_a_failure():
    """Poor is an observation. Its companion 0.0ms is not, so it is not shown."""
    gate = gates.sfu_capacity_gate([_timed_out(2, "Poor")], 50.0, None)
    assert gate.status == gates.FAIL
    assert "quality Poor" in gate.detail
    assert "no rtt reading" in gate.detail
    assert "rtt 0.0ms" not in gate.detail
    assert gate.value is None


def test_a_step_from_an_older_run_without_a_sample_count_is_not_mislabelled():
    """Absent is not zero: archived ramps must not all turn UNKNOWN.

    ``_step`` is the pre-``samples`` shape, which is what every stored run made
    before the count existed looks like.
    """
    assert "samples" not in _step(2, 11.0)
    healthy = gates.sfu_capacity_gate([_step(2, 11.0), _step(10, 14.0)], 50.0, None)
    assert healthy.status == gates.PASS
    assert healthy.value == 11.0

    breached = gates.sfu_capacity_gate([_step(2, 90.0, "Poor")], 50.0, None)
    assert breached.status == gates.FAIL

    # With no count to key on, the number itself decides, under the same rule
    # _has_measurement applies to reply latency: an rtt of 0.0 through an SFU is
    # not a time. A legacy total failure must not read as a fast one either.
    dead = gates.sfu_capacity_gate([_step(2, 0.0, "Unknown")], 50.0, None)
    assert dead.status == gates.UNKNOWN
    assert "no sample count" in dead.detail

    # ... including when the connection quality read fine and only the pings
    # never came back.
    quiet = gates.sfu_capacity_gate([_step(2, 0.0, "Excellent")], 50.0, None)
    assert quiet.status == gates.UNKNOWN
    assert quiet.value is None

    # A legacy step with no rtt key at all is not a 0.0 either.
    no_rtt = gates.sfu_capacity_gate(
        [{"clients": 2, "quality": "Excellent"}], 50.0, None
    )
    assert no_rtt.status == gates.UNKNOWN


def test_an_unusable_sample_count_falls_back_instead_of_crashing():
    """A null or junk ``samples`` is no count at all, not a count of zero."""
    for junk in (None, "", "abc", [], {}):
        step = dict(_step(2, 11.0))
        step["samples"] = junk
        gate = gates.sfu_capacity_gate([step], 50.0, None)
        assert gate.status == gates.PASS  # rtt 11.0 is still a reading

        dead = dict(_step(2, 0.0, "Unknown"))
        dead["samples"] = junk
        assert gates.sfu_capacity_gate([dead], 50.0, None).status == gates.UNKNOWN

    # A count that is a string of digits is still a count.
    counted = dict(_step(2, 11.0))
    counted["samples"] = "3"
    assert gates.sfu_capacity_gate([counted], 50.0, None).status == gates.PASS


# ---------------------------------------------------------------------------
# The whole-run shape
# ---------------------------------------------------------------------------


def test_gate_results_are_json_safe():
    checks = {"agents": {"ok": True, "result": {"agents": []}}}
    [gate] = gates.evaluate_checks(checks, 1500.0)
    payload = gate.as_dict()
    assert set(payload) == {
        "gate",
        "status",
        "detail",
        "subject",
        "metric",
        "value",
        "threshold",
    }
    import json

    json.loads(json.dumps(payload))


def test_the_run_verdict_is_the_worst_gate():
    checks = {
        "agents": {"ok": True, "result": {"agents": []}},
        "sfu": {
            "ok": True,
            "result": {"baseline": {"rtt_ms": 90.0, "quality": "Poor"}},
        },
        "latency": {
            "ok": True,
            "result": {"agents": [{"agent": "a", "stats": _stats(0.5, 0.6, 2)}]},
        },
    }
    results = gates.evaluate_checks(checks, 1500.0)
    assert [g.status for g in results] == [gates.PASS, gates.FAIL, gates.PASS]
    assert gates.verdict(results) == gates.FAIL


# ---------------------------------------------------------------------------
# A baseline that measured nothing: quality and rtt are INDEPENDENT readings
#
# quality is the SDK's own peer-connection metric; rtt_ms is a mean over ping
# round trips. A connection that came up while every ping timed out reports
# "Excellent" beside 0.0ms over 0 samples, and "Excellent" is not falsy, not
# _NO_QUALITY and not degraded, so the gate fell through to PASS.
# ---------------------------------------------------------------------------


def _baseline(rtt: float, samples: int, quality: str = "Excellent") -> dict:
    """A baseline as ``service.sfu`` publishes it, with its sample count."""
    return {
        "rtt_ms": rtt,
        "loss_pct": 0.0,
        "quality": quality,
        "samples": samples,
        "rtt_stat": "mean_of_n" if samples else "not_measured",
    }


def test_an_excellent_baseline_where_every_ping_timed_out_does_not_pass():
    """The bug F6 removed from the ramp, still live in the baseline gate."""
    gate = gates.sfu_quality_gate(_baseline(0.0, 0))
    assert gate.status == gates.UNKNOWN
    assert gate.status != gates.PASS
    assert "measured nothing" in gate.detail
    assert "samples 0" in gate.detail
    # The 0.0 is the mean of an empty list, so it is never printed as a time.
    assert "rtt 0.0ms" not in gate.detail
    # Nothing decided it, so no metric is claimed.
    assert gate.metric is None and gate.value is None
    # UNKNOWN is not the soft option: the run still exits non-zero.
    assert gates.exit_code(gates.verdict([gate])) == 1


def test_a_measured_baseline_passes_exactly_as_before():
    gate = gates.sfu_quality_gate(_baseline(11.0, 2))
    assert gate.status == gates.PASS
    assert gate.detail == "SFU baseline connection quality is Excellent (rtt 11.0ms)"
    assert gate.metric == "sfu_baseline_quality"
    # A single round trip is still a round trip.
    assert gates.sfu_quality_gate(_baseline(11.0, 1)).status == gates.PASS


def test_a_degraded_baseline_that_measured_no_rtt_is_still_a_failure():
    """Matches the ramp: Poor/Lost is an observation, an absent rtt is not.

    ``sfu_capacity_gate`` FAILs an unmeasured tier that reported Poor rather
    than calling it UNKNOWN, and the two gates read the same probe, so they
    answer the same way. Its 0.0ms is not a reading either way, so it is not
    printed as one.
    """
    for quality in ("Poor", "Lost"):
        gate = gates.sfu_quality_gate(_baseline(0.0, 0, quality))
        assert gate.status == gates.FAIL, quality
        assert f"quality is {quality}" in gate.detail
        assert "no rtt reading" in gate.detail
        assert "rtt 0.0ms" not in gate.detail
    # And a degraded baseline that DID measure still prints its rtt.
    assert "rtt 90.0ms" in gates.sfu_quality_gate(_baseline(90.0, 2, "Poor")).detail


def test_a_baseline_from_an_older_run_without_a_sample_count_is_not_mislabelled():
    """Absent is not zero: archived runs must not all turn UNKNOWN."""
    legacy = {"rtt_ms": 11.0, "loss_pct": 0.0, "quality": "Excellent"}
    assert "samples" not in legacy
    assert gates.sfu_quality_gate(legacy).status == gates.PASS

    degraded = {"rtt_ms": 90.0, "loss_pct": 0.0, "quality": "Poor"}
    assert gates.sfu_quality_gate(degraded).status == gates.FAIL

    # With no count to key on, the number itself decides, under the same rule
    # _has_measurement applies to reply latency: an rtt of 0.0 through an SFU is
    # not a time, so a legacy total failure must not read as a fast one either.
    dead = {"rtt_ms": 0.0, "loss_pct": 0.0, "quality": "Excellent"}
    gate = gates.sfu_quality_gate(dead)
    assert gate.status == gates.UNKNOWN
    assert "no sample count" in gate.detail
    assert gate.value is None

    # A legacy baseline with no rtt key at all is not a 0.0 either.
    assert gates.sfu_quality_gate({"quality": "Excellent"}).status == gates.UNKNOWN
    # ... and a null rtt does not raise on the way there.
    assert (
        gates.sfu_quality_gate({"quality": "Excellent", "rtt_ms": None}).status
        == gates.UNKNOWN
    )


def test_an_unusable_baseline_sample_count_falls_back_instead_of_crashing():
    """A null or junk ``samples`` is no count at all, not a count of zero."""
    for junk in (None, "", "abc", [], {}):
        alive = {"rtt_ms": 11.0, "quality": "Excellent", "samples": junk}
        assert gates.sfu_quality_gate(alive).status == gates.PASS  # 11.0 is a reading

        dead = {"rtt_ms": 0.0, "quality": "Excellent", "samples": junk}
        assert gates.sfu_quality_gate(dead).status == gates.UNKNOWN

    # A count that is a string of digits is still a count.
    counted = {"rtt_ms": 11.0, "quality": "Excellent", "samples": "3"}
    assert gates.sfu_quality_gate(counted).status == gates.PASS


def test_an_unmeasured_baseline_is_unknown_through_the_whole_run():
    checks = {
        "sfu_load": {
            "ok": True,
            "result": {
                # The connection was fine; not one ping came back.
                "baseline": _baseline(0.0, 0),
                "ramp": [_measured(2, 11.0, 3)],
                "target_rtt_ms": 50.0,
                "resource": None,
            },
        }
    }
    results = gates.evaluate_checks(checks, 1500.0)
    quality = [g for g in results if g.gate == gates.SFU_QUALITY_GATE]
    assert [g.status for g in quality] == [gates.UNKNOWN]
    # The ramp still measured something, so this is the baseline's verdict alone.
    assert [g.status for g in results if g.gate == gates.SFU_CAPACITY_GATE] == [
        gates.PASS
    ]
    assert gates.verdict(results) != gates.PASS

    import json

    for gate in results:
        json.loads(json.dumps(gate.as_dict()))


# ---------------------------------------------------------------------------
# establishment_gate: at least 99.5% of call attempts must establish
#
# The acceptance criterion this gate encodes is a share of attempts, so the one
# input that must never read as healthy is a run that attempted nothing. Zero
# attempts also means zero failures, and every "failures within budget" phrasing
# calls that run perfect. These pin UNKNOWN over PASS for every such shape.
# ---------------------------------------------------------------------------


def test_a_run_above_the_bar_passes():
    gate = gates.establishment_gate(attempted=15000, succeeded=14985)
    assert gate.status == gates.PASS
    assert gate.gate == gates.ESTABLISHMENT_GATE
    assert gate.metric == "establishment_ratio"
    assert gate.value == 14985 / 15000
    assert gate.threshold == gates.MIN_ESTABLISHMENT_RATIO
    assert "14985 of 15000" in gate.detail


def test_the_bar_is_inclusive_at_every_scale():
    """Exactly 99.5% passes, and float division must not lose the boundary."""
    for attempted, succeeded in ((200, 199), (2000, 1990), (20000, 19900)):
        gate = gates.establishment_gate(attempted=attempted, succeeded=succeeded)
        assert gate.status == gates.PASS, (attempted, succeeded, gate.detail)
        assert gate.value == succeeded / attempted


def test_one_call_below_the_bar_fails():
    gate = gates.establishment_gate(attempted=1000, succeeded=994)
    assert gate.status == gates.FAIL
    assert gate.value == 0.994
    assert "below" in gate.detail
    assert gates.exit_code(gates.verdict([gate])) == 1


def test_a_run_that_attempted_nothing_is_unknown_not_pass():
    """The whole reason this gate reports UNKNOWN rather than a clean rate."""
    gate = gates.establishment_gate(attempted=0, succeeded=0)
    assert gate.status == gates.UNKNOWN
    assert gate.status != gates.PASS
    # The detail must say why zero attempts is not success, because "0 failures"
    # is exactly what a reader would otherwise take from it.
    assert "no failures" in gate.detail
    # Nothing decided it, so no number is claimed.
    assert gate.metric is None
    assert gate.value is None
    # The bar is still known even though nothing was measured against it.
    assert gate.threshold == gates.MIN_ESTABLISHMENT_RATIO
    assert gates.exit_code(gates.verdict([gate])) == 1


def test_absent_counts_are_unknown_and_name_which_one_is_missing():
    both = gates.establishment_gate(attempted=None, succeeded=None)
    assert both.status == gates.UNKNOWN
    assert "attempt and success counts" in both.detail

    no_success = gates.establishment_gate(attempted=15000, succeeded=None)
    assert no_success.status == gates.UNKNOWN
    assert "success count" in no_success.detail

    no_attempt = gates.establishment_gate(attempted=None, succeeded=14985)
    assert no_attempt.status == gates.UNKNOWN
    assert "attempt count" in no_attempt.detail


def test_counts_that_cannot_describe_a_run_are_unknown():
    more = gates.establishment_gate(attempted=100, succeeded=101)
    assert more.status == gates.UNKNOWN
    assert "do not describe a run" in more.detail

    negative = gates.establishment_gate(attempted=100, succeeded=-1)
    assert negative.status == gates.UNKNOWN

    negative_attempts = gates.establishment_gate(attempted=-5, succeeded=0)
    assert negative_attempts.status == gates.UNKNOWN


def test_a_bool_is_not_a_call_count():
    """bool is an int subclass, so True would otherwise be an attempt count."""
    gate = gates.establishment_gate(attempted=True, succeeded=True)
    assert gate.status == gates.UNKNOWN
    assert gate.value is None


def test_no_unmeasured_shape_can_ever_pass():
    """One sweep over every shape that carries no usable measurement."""
    unmeasured = [
        {"attempted": None, "succeeded": None},
        {"attempted": None, "succeeded": 10},
        {"attempted": 10, "succeeded": None},
        {"attempted": 0, "succeeded": 0},
        {"attempted": 0, "succeeded": 5},
        {"attempted": -1, "succeeded": 0},
        {"attempted": 100, "succeeded": -1},
        {"attempted": 100, "succeeded": 101},
        {"attempted": True, "succeeded": False},
        {"attempted": "many", "succeeded": "most"},
        {"attempted": [], "succeeded": {}},
    ]
    for kwargs in unmeasured:
        gate = gates.establishment_gate(**kwargs)
        assert gate.status == gates.UNKNOWN, kwargs
        assert gate.status != gates.PASS, kwargs
        assert gate.value is None, kwargs
        assert gates.exit_code(gates.verdict([gate])) == 1, kwargs


def test_the_threshold_is_configurable_but_defaults_to_the_acceptance_bar():
    assert gates.MIN_ESTABLISHMENT_RATIO == 0.995
    # A stricter bar the same run now fails.
    strict = gates.establishment_gate(attempted=1000, succeeded=996, threshold=0.999)
    assert strict.status == gates.FAIL
    assert strict.threshold == 0.999
    assert "99.9% bar" in strict.detail
    # The same run against the shipped bar passes.
    assert gates.establishment_gate(attempted=1000, succeeded=996).status == gates.PASS


def test_the_subject_names_which_test_was_judged():
    gate = gates.establishment_gate(attempted=1000, succeeded=999, subject="ramp-500")
    assert gate.subject == "ramp-500"


def test_the_gate_is_synchronous_and_json_safe():
    import inspect
    import json

    assert not inspect.iscoroutinefunction(gates.establishment_gate)
    for gate in (
        gates.establishment_gate(attempted=100, succeeded=100),
        gates.establishment_gate(attempted=0, succeeded=0),
    ):
        json.loads(json.dumps(gate.as_dict()))


def test_a_non_finite_count_is_unknown_not_a_traceback():
    """``json.loads`` accepts the ``Infinity`` token, so this is reachable.

    ``int(float('inf'))`` raises OverflowError, not ValueError, so a summary
    carrying a non-finite count would have escaped the gate as a traceback
    instead of an UNKNOWN verdict.
    """
    for bad in (float("inf"), float("-inf"), float("nan")):
        gate = gates.establishment_gate(attempted=bad, succeeded=1)
        assert gate.status == gates.UNKNOWN, bad
        assert gate.value is None, bad
        gate = gates.establishment_gate(attempted=100, succeeded=bad)
        assert gate.status == gates.UNKNOWN, bad


# ---------------------------------------------------------------------------
# node_cpu_gates / node_memory_gates: per-node resource ceilings
#
# The ceilings are STRICT ("CPU below 70%", "memory below 75%"), the opposite
# direction to establishment_gate's inclusive floor. Sitting exactly on a ceiling
# has not stayed below it. Reusing one gate's comparison for the other is the easy
# mistake, so both directions are pinned.
# ---------------------------------------------------------------------------


def _reading(node: str, utilisation, **kw) -> gates.NodeUtilisationReading:
    kw.setdefault("samples", 12)
    return gates.NodeUtilisationReading(node=node, utilisation=utilisation, **kw)


def test_a_node_under_both_ceilings_passes():
    [cpu] = gates.node_cpu_gates([_reading("sfu-1", 0.42)])
    assert cpu.status == gates.PASS
    assert cpu.gate == gates.NODE_CPU_GATE
    assert cpu.subject == "sfu-1"
    assert cpu.value == 0.42
    assert cpu.threshold == gates.MAX_NODE_CPU_UTILISATION
    assert "42.0%" in cpu.detail

    [mem] = gates.node_memory_gates([_reading("sfu-1", 0.60)])
    assert mem.status == gates.PASS
    assert mem.threshold == gates.MAX_NODE_MEMORY_UTILISATION


def test_the_ceilings_are_strict_so_sitting_exactly_on_one_fails():
    """ "Below 70%" is not satisfied by 70%. The opposite of the 99.5% floor."""
    assert gates.MAX_NODE_CPU_UTILISATION == 0.70
    assert gates.MAX_NODE_MEMORY_UTILISATION == 0.75
    [cpu] = gates.node_cpu_gates([_reading("sfu-1", 0.70)])
    assert cpu.status == gates.FAIL
    [mem] = gates.node_memory_gates([_reading("sfu-1", 0.75)])
    assert mem.status == gates.FAIL
    # A hair under still passes, so the boundary is exactly where it claims.
    assert gates.node_cpu_gates([_reading("s", 0.6999)])[0].status == gates.PASS
    assert gates.node_memory_gates([_reading("s", 0.7499)])[0].status == gates.PASS


def test_the_two_ceilings_are_not_interchangeable():
    """72% is fine for memory and a failure for CPU. One number, two verdicts."""
    assert gates.node_cpu_gates([_reading("s", 0.72)])[0].status == gates.FAIL
    assert gates.node_memory_gates([_reading("s", 0.72)])[0].status == gates.PASS


def test_a_node_over_a_ceiling_fails_and_exits_non_zero():
    [cpu] = gates.node_cpu_gates([_reading("sip-2", 0.91)])
    assert cpu.status == gates.FAIL
    assert "at or above" in cpu.detail
    assert gates.exit_code(gates.verdict([cpu])) == 1


def test_one_gate_per_node_never_an_average():
    """A mean across the fleet hides the one node that saturated."""
    results = gates.node_cpu_gates(
        [_reading("sfu-1", 0.10), _reading("sfu-2", 0.12), _reading("sfu-3", 0.95)]
    )
    assert len(results) == 3
    assert [g.status for g in results] == [gates.PASS, gates.PASS, gates.FAIL]
    assert gates.verdict(results) == gates.FAIL


def test_an_unmeasured_node_is_unknown_not_pass():
    [gate] = gates.node_cpu_gates(
        [_reading("sfu-1", None, samples=0, unmeasured_reason="every scrape timed out")]
    )
    assert gate.status == gates.UNKNOWN
    assert gate.status != gates.PASS
    assert "every scrape timed out" in gate.detail
    assert "not the same as staying under one" in gate.detail
    # Nothing decided it, so no number is claimed.
    assert gate.metric is None
    assert gate.value is None
    assert gates.exit_code(gates.verdict([gate])) == 1


def test_a_window_nobody_sampled_is_unknown_not_an_idle_fleet():
    """no_samples and scrape_failed both land here. Neither is a quiet node."""
    for fn in (gates.node_cpu_gates, gates.node_memory_gates):
        [gate] = fn([])
        assert gate.status == gates.UNKNOWN
        assert "not a quiet fleet" in gate.detail
        assert gate.value is None


def test_zero_utilisation_is_a_reading_and_none_is_not():
    """An idle node passes on evidence; an unscraped one has no evidence."""
    [idle] = gates.node_cpu_gates([_reading("sfu-1", 0.0)])
    assert idle.status == gates.PASS
    assert idle.value == 0.0
    [unscraped] = gates.node_cpu_gates([_reading("sfu-1", None, samples=0)])
    assert unscraped.status == gates.UNKNOWN


def test_the_sample_count_travels_so_a_peak_over_two_is_not_dressed_up():
    [gate] = gates.node_cpu_gates([_reading("sfu-1", 0.5, samples=2)])
    assert "2 measured sample(s)" in gate.detail


def test_the_source_disambiguates_two_scrapes_of_one_node():
    [gate] = gates.node_cpu_gates([_reading("node-a", 0.5, source="node-exporter")])
    assert gate.subject == "node-a/node-exporter"


def test_the_node_gates_are_synchronous_and_json_safe():
    import inspect
    import json

    assert not inspect.iscoroutinefunction(gates.node_cpu_gates)
    assert not inspect.iscoroutinefunction(gates.node_memory_gates)
    for gate in gates.node_cpu_gates([_reading("n", 0.5), _reading("m", None)]):
        json.loads(json.dumps(gate.as_dict()))


# ---------------------------------------------------------------------------
# headroom_gates: at least 20% of a limited resource must stay free
#
# A third threshold DIRECTION in this module, and the reason each one is pinned
# separately: establishment is an inclusive floor on a ratio, CPU and memory are
# strict ceilings on a utilisation, and headroom is an inclusive floor on what is
# left. The criterion names three resources and only one is scraped.
# ---------------------------------------------------------------------------


def _fd(node: str, used, limit, **kw) -> gates.HeadroomReading:
    return gates.HeadroomReading(
        node=node,
        resource=gates.HEADROOM_FILE_DESCRIPTORS,
        used=used,
        limit=limit,
        **kw,
    )


def test_a_node_with_room_to_spare_passes():
    [gate] = gates.headroom_gates([_fd("sfu-1", 400_000, 1_048_576)])
    assert gate.status == gates.PASS
    assert gate.gate == gates.HEADROOM_GATE
    assert gate.subject == "sfu-1/file_descriptors"
    assert gate.threshold == gates.MIN_HEADROOM_FRACTION
    # The raw counts travel, so a reader can check the percentage.
    assert "400000 of 1048576 used" in gate.detail


def test_the_headroom_floor_is_inclusive_at_exactly_twenty_percent():
    """ "At least 20% headroom" is met by exactly 20%."""
    assert gates.MIN_HEADROOM_FRACTION == 0.20
    [exact] = gates.headroom_gates([_fd("sfu-1", 800, 1000)])
    assert exact.status == gates.PASS
    assert exact.value == pytest.approx(0.20)
    # One descriptor further along and the floor is breached.
    [under] = gates.headroom_gates([_fd("sfu-1", 801, 1000)])
    assert under.status == gates.FAIL
    assert "below" in under.detail


def test_headroom_is_a_floor_where_cpu_is_a_ceiling():
    """The same shape of number, judged in opposite directions.

    80% of the file-descriptor limit in use leaves exactly the required headroom
    and passes. 80% CPU is over its ceiling and fails. Reusing one comparison for
    the other would invert one of them silently.
    """
    [fd] = gates.headroom_gates([_fd("n", 80, 100)])
    assert fd.status == gates.PASS
    [cpu] = gates.node_cpu_gates([_reading("n", 0.80)])
    assert cpu.status == gates.FAIL


def test_the_sizing_margin_is_not_this_threshold():
    """0.85 in the node-count formula is a different number for a different job.

    It is a 15% CPU margin reserved so the fleet still carries its target after
    losing a node. This is 20% of a limited resource left unused during the run.
    Pinned here because the two are close enough to invite a reconciliation.
    """
    assert gates.MIN_HEADROOM_FRACTION == 0.20
    assert gates.MIN_HEADROOM_FRACTION != 0.15
    # Judged as headroom remaining, never as an 0.80 utilisation ceiling.
    [gate] = gates.headroom_gates([_fd("n", 80, 100)])
    assert gate.metric == "file_descriptors_headroom"
    assert gate.value == pytest.approx(0.20)


def test_a_caller_that_scraped_nothing_files_three_not_measured_rows():
    """A caller with no scrape for these files them as unmeasured, not absent.

    WHAT CHANGED. This used to assert TWO rows, ``rtp_ports`` and a single
    ``network``, on the reasoning that nothing in the system could ever scrape
    either. Both halves of that became false. RTP ports are now measured from
    ``media_ports_in_use``/``media_ports_total``, and ``network`` was split into
    :data:`gates.HEADROOM_NETWORK_IN` and :data:`gates.HEADROOM_NETWORK_OUT`
    because a cloud instance meters ingress and egress against SEPARATE credit
    buckets and one row sharing a subject would have to silently pick a
    direction. So the old two-element assertion is now wrong in its membership
    AND in its length.

    What this helper still is, and what is still asserted exactly: the fallback
    a caller uses when it scraped none of them, so an unmeasured resource files
    a row saying so instead of vanishing. Omitting them would show one green row
    and read as full coverage of a three-part requirement.
    """
    readings = gates.unscraped_headroom_readings("sfu-1")
    assert [r.resource for r in readings] == [
        gates.HEADROOM_RTP_PORTS,
        gates.HEADROOM_NETWORK_IN,
        gates.HEADROOM_NETWORK_OUT,
    ]
    results = gates.headroom_gates(readings)
    assert [g.status for g in results] == [
        gates.UNKNOWN,
        gates.UNKNOWN,
        gates.UNKNOWN,
    ]
    for gate in results:
        assert "nothing measures it" in gate.detail
        assert "not spare capacity" in gate.detail
        assert gate.value is None
    # Every row identifies itself, which is the reason the direction split is
    # two resources rather than one: two subjects, two answers, no collision.
    assert [g.subject for g in results] == [
        "sfu-1/rtp_ports",
        "sfu-1/network_in",
        "sfu-1/network_out",
    ]
    assert len({g.subject for g in results}) == 3
    # THE COMPANION. pps is a PERMANENT scope exclusion (no per-instance-type
    # allowance is published by anyone, so there is no denominator), and it must
    # never be filed here: an unmeasured-headroom row puts a resource in the
    # queue of things somebody could fix by wiring an exporter, and nobody can.
    assert gates.HEADROOM_PPS not in {r.resource for r in readings}
    assert gates.exit_code(gates.verdict(results)) == 1


def test_an_absent_pair_is_unknown_not_full_headroom():
    for used, limit in ((None, 1000), (500, None), (None, None)):
        [gate] = gates.headroom_gates([_fd("n", used, limit)])
        assert gate.status == gates.UNKNOWN, (used, limit)
        assert gate.value is None


def test_counts_that_do_not_describe_a_limit_are_unknown():
    """A zero ceiling has no headroom to have, and used>limit is incoherent."""
    for used, limit in ((0, 0), (10, 0), (-1, 100), (101, 100)):
        [gate] = gates.headroom_gates([_fd("n", used, limit)])
        assert gate.status == gates.UNKNOWN, (used, limit)
        assert "do not describe a limit" in gate.detail


def test_a_fully_free_resource_passes_and_a_saturated_one_fails():
    assert gates.headroom_gates([_fd("n", 0, 100)])[0].status == gates.PASS
    saturated = gates.headroom_gates([_fd("n", 100, 100)])[0]
    assert saturated.status == gates.FAIL
    assert saturated.value == pytest.approx(0.0)


def test_no_readings_at_all_is_unknown_not_room_to_spare():
    [gate] = gates.headroom_gates([])
    assert gate.status == gates.UNKNOWN
    assert "not room to spare" in gate.detail


def test_one_gate_per_node_and_resource():
    # FIVE, not four. unscraped_headroom_readings grew a third row when network
    # split into an inbound and an outbound resource, and one gate per (node,
    # resource) means the row count follows the resource count exactly.
    results = gates.headroom_gates(
        [_fd("sfu-1", 10, 100), _fd("sfu-2", 95, 100)]
        + gates.unscraped_headroom_readings("sfu-1")
    )
    assert len(results) == 5
    assert [g.status for g in results] == [
        gates.PASS,
        gates.FAIL,
        gates.UNKNOWN,
        gates.UNKNOWN,
        gates.UNKNOWN,
    ]
    # One gate per (node, resource) means no two rows share a subject.
    assert len({g.subject for g in results}) == len(results)
    assert gates.verdict(results) == gates.FAIL


def test_headroom_gates_are_synchronous_and_json_safe():
    import inspect
    import json

    assert not inspect.iscoroutinefunction(gates.headroom_gates)
    for gate in gates.headroom_gates(
        [_fd("n", 10, 100)] + gates.unscraped_headroom_readings("n")
    ):
        json.loads(json.dumps(gate.as_dict()))


# ---------------------------------------------------------------------------
# return_to_baseline_gates: did the fleet give its resources back after teardown?
#
# heap_inuse and goroutines, never RSS: Go returns freed heap to the OS lazily, so
# a drained process holds its resident size and an RSS gate would report a leak on
# every healthy run.
# ---------------------------------------------------------------------------


def _cmp(metric: str, baseline, post, **kw) -> gates.BaselineComparison:
    kw.setdefault("node", "sfu-1")
    return gates.BaselineComparison(
        metric=metric, baseline=baseline, post_settle=post, **kw
    )


def test_a_fleet_that_gave_its_memory_back_passes():
    [gate] = gates.return_to_baseline_gates(
        [_cmp(gates.BASELINE_HEAP, 100_000_000, 108_000_000)], tolerance=1.5
    )
    assert gate.status == gates.PASS
    assert gate.gate == gates.RETURN_TO_BASELINE_GATE
    assert gate.subject == "sfu-1/heap_inuse_bytes"
    assert gate.value == pytest.approx(1.08)
    assert gate.threshold == 1.5
    assert "1.08x" in gate.detail


def test_a_goroutine_count_that_never_came_down_fails():
    """The shape a real leak takes here: a per-call goroutine that never exits."""
    [gate] = gates.return_to_baseline_gates(
        [_cmp(gates.BASELINE_GOROUTINES, 120, 4_800)], tolerance=1.5
    )
    assert gate.status == gates.FAIL
    assert "outside" in gate.detail
    assert gates.exit_code(gates.verdict([gate])) == 1


def test_the_tolerance_is_required_and_has_no_default():
    """Near baseline is never quantified by the criterion, so nothing here may
    invent a number that would look exactly like a contracted threshold."""
    import inspect

    sig = inspect.signature(gates.return_to_baseline_gates)
    tolerance = sig.parameters["tolerance"]
    assert tolerance.default is inspect.Parameter.empty
    assert tolerance.kind is inspect.Parameter.KEYWORD_ONLY
    with pytest.raises(TypeError):
        gates.return_to_baseline_gates([_cmp(gates.BASELINE_HEAP, 10, 10)])


def test_the_tolerance_travels_on_every_result():
    """A reader must see which tolerance produced the verdict."""
    for tol in (1.1, 2.0):
        [gate] = gates.return_to_baseline_gates(
            [_cmp(gates.BASELINE_HEAP, 100, 150)], tolerance=tol
        )
        assert gate.threshold == tol
    # The same pair, judged either way, depending on the number supplied.
    assert (
        gates.return_to_baseline_gates(
            [_cmp(gates.BASELINE_HEAP, 100, 150)], tolerance=1.1
        )[0].status
        == gates.FAIL
    )
    assert (
        gates.return_to_baseline_gates(
            [_cmp(gates.BASELINE_HEAP, 100, 150)], tolerance=2.0
        )[0].status
        == gates.PASS
    )


def test_a_sample_taken_before_the_settle_window_is_unknown_not_pass():
    """Teardown that has not finished draining looks exactly like a clean one."""
    assert gates.MIN_SETTLE_MS == 300_000
    [early] = gates.return_to_baseline_gates(
        [
            _cmp(
                gates.BASELINE_HEAP,
                100,
                100,
                baseline_at_ms=0,
                post_settle_at_ms=60_000,
            )
        ],
        tolerance=1.5,
    )
    assert early.status == gates.UNKNOWN
    assert early.status != gates.PASS
    assert "settle window" in early.detail
    assert "60s after the first" in early.detail
    # Past the window, the very same numbers pass.
    [settled] = gates.return_to_baseline_gates(
        [
            _cmp(
                gates.BASELINE_HEAP,
                100,
                100,
                baseline_at_ms=0,
                post_settle_at_ms=600_000,
            )
        ],
        tolerance=1.5,
    )
    assert settled.status == gates.PASS


def test_timestamps_are_optional_and_absent_ones_do_not_fabricate_a_settle():
    """No timestamps means the settle check cannot run, not that it passed."""
    [gate] = gates.return_to_baseline_gates(
        [_cmp(gates.BASELINE_HEAP, 100, 100)], tolerance=1.5
    )
    assert gate.status == gates.PASS
    assert "settle window" not in gate.detail


def test_a_missing_side_is_unknown_not_a_clean_return():
    for baseline, post in ((None, 100), (100, None), (None, None)):
        [gate] = gates.return_to_baseline_gates(
            [_cmp(gates.BASELINE_HEAP, baseline, post)], tolerance=1.5
        )
        assert gate.status == gates.UNKNOWN, (baseline, post)
        assert "given back" in gate.detail
        assert gate.value is None


def test_a_zero_baseline_is_not_a_level_to_return_to():
    for baseline in (0, -1):
        [gate] = gates.return_to_baseline_gates(
            [_cmp(gates.BASELINE_GOROUTINES, baseline, 10)], tolerance=1.5
        )
        assert gate.status == gates.UNKNOWN
        assert "not a level to return to" in gate.detail


def test_no_pair_at_all_is_unknown_not_a_clean_teardown():
    [gate] = gates.return_to_baseline_gates([], tolerance=1.5)
    assert gate.status == gates.UNKNOWN
    assert "not a clean teardown" in gate.detail


def test_rss_is_not_one_of_the_measured_series():
    """Pinned so nobody adds it later: it reports a leak on healthy runs."""
    assert gates.BASELINE_HEAP == "heap_inuse_bytes"
    assert gates.BASELINE_GOROUTINES == "go_goroutines"
    assert "rss" not in gates.BASELINE_HEAP.lower()
    assert "resident" not in gates.BASELINE_HEAP.lower()


def test_return_to_baseline_is_synchronous_and_json_safe():
    import inspect
    import json

    assert not inspect.iscoroutinefunction(gates.return_to_baseline_gates)
    for gate in gates.return_to_baseline_gates(
        [_cmp(gates.BASELINE_HEAP, 100, 110), _cmp(gates.BASELINE_GOROUTINES, None, 5)],
        tolerance=1.5,
    ):
        json.loads(json.dumps(gate.as_dict()))


# ---------------------------------------------------------------------------
# WAIVED: a threshold the run was not held to, recorded rather than dropped
#
# The requirement is "record the CPU/headroom gates as waived in writing, never a
# silent pass". Every assertion here is about the second half of that sentence.
# ---------------------------------------------------------------------------


def _failing() -> gates.GateResult:
    return gates.GateResult(
        gate=gates.NODE_CPU_GATE,
        status=gates.FAIL,
        detail="peak CPU on sfu-1 was 91.0%",
        subject="sfu-1",
        metric="node_cpu_utilisation",
        value=0.91,
        threshold=0.70,
    )


def test_a_waiver_requires_a_reason():
    """An unexplained waiver is a silently dropped gate wearing a label."""
    for blank in ("", "   ", "\n\t "):
        with pytest.raises(ValueError, match="requires a reason"):
            gates.waive(_failing(), reason=blank)


def test_a_waiver_never_collapses_to_pass():
    waived = gates.waive(_failing(), reason="instrumentation not funded")
    assert waived.status == gates.WAIVED
    assert waived.status != gates.PASS
    assert gates.verdict([waived]) == gates.WAIVED
    assert gates.verdict([waived]) != gates.PASS


def test_exit_code_does_not_treat_a_waiver_as_clean():
    """A pipeline that goes green on a waiver has dropped the requirement."""
    assert gates.exit_code(gates.WAIVED) == 1
    waived = gates.waive(_failing(), reason="instrumentation not funded")
    assert gates.exit_code(gates.verdict([waived])) == 1
    # Even alongside otherwise-clean gates.
    clean = gates.GateResult(gate="x", status=gates.PASS, detail="fine")
    assert gates.verdict([clean, waived]) == gates.WAIVED
    assert gates.exit_code(gates.verdict([clean, waived])) == 1


def test_waived_outranks_pass_and_loses_to_a_measured_problem():
    """Above PASS: a gate nobody enforced was not satisfied.

    Below WARN: a WARN is an observed degradation, which is worse news than a
    threshold somebody agreed in writing not to hold this run to.
    """
    assert gates.worst_status([gates.PASS, gates.WAIVED]) == gates.WAIVED
    assert gates.worst_status([gates.WAIVED, gates.WARN]) == gates.WARN
    assert gates.worst_status([gates.WAIVED, gates.UNKNOWN]) == gates.UNKNOWN
    assert gates.worst_status([gates.WAIVED, gates.FAIL]) == gates.FAIL


def test_the_reason_is_carried_structurally_and_in_the_prose():
    """A surface that renders one and not the other still shows the reason."""
    waived = gates.waive(_failing(), reason="CPU exporter not funded for this run")
    assert waived.waiver_reason == "CPU exporter not funded for this run"
    assert "CPU exporter not funded for this run" in waived.detail
    # It survives the JSON round trip the payload is persisted through.
    import json

    assert (
        json.loads(json.dumps(waived.as_dict()))["waiver_reason"]
        == "CPU exporter not funded for this run"
    )


def test_the_waived_status_keeps_what_it_would_otherwise_have_been():
    """Would-have-failed and would-have-passed-anyway are different facts,
    and only the first one is a risk somebody accepted."""
    waived = gates.waive(_failing(), reason="r")
    assert "Would otherwise have been FAIL" in waived.detail
    passing = gates.GateResult(gate="g", status=gates.PASS, detail="fine")
    assert "Would otherwise have been PASS" in gates.waive(passing, reason="r").detail


def test_a_waiver_preserves_the_number_that_was_measured():
    """The measurement is not erased by the decision not to enforce it."""
    waived = gates.waive(_failing(), reason="not funded")
    assert waived.value == 0.91
    assert waived.threshold == 0.70
    assert waived.metric == "node_cpu_utilisation"
    assert waived.subject == "sfu-1"
    assert waived.gate == gates.NODE_CPU_GATE


def test_the_reason_is_stripped_but_not_otherwise_altered():
    waived = gates.waive(_failing(), reason="  agreed on 2026-07-31  ")
    assert waived.waiver_reason == "agreed on 2026-07-31"


def test_a_waiver_shows_up_in_the_summary_lines():
    [line] = gates.summary_lines([gates.waive(_failing(), reason="not funded")])
    assert "[WAIVED]" in line
    assert "not funded" in line


def test_the_prometheus_exposition_does_not_drop_a_waived_gate():
    """The filter there DROPS unrecognised statuses, and an absent gate reads as
    one that never ran rather than one somebody chose not to enforce."""
    from voicegateway.server.api import metrics as metrics_api

    assert gates.WAIVED in metrics_api._GATE_STATUSES


def test_the_report_renders_waived_distinctly_from_unknown():
    """Falling through to the unknown class would say "could not evaluate"."""
    from voicegateway.livekit_diag import run_report

    assert gates.WAIVED in run_report._VERDICT_MEANING
    assert "NOT a pass" in run_report._VERDICT_MEANING[gates.WAIVED]
    assert ".tag.waived" in run_report._CSS if hasattr(run_report, "_CSS") else True


# ---------------------------------------------------------------------------
# return_to_baseline: a ratio needs a denominator worth dividing by
# ---------------------------------------------------------------------------


def _settled_cmp(metric: str, baseline: float, post: float):
    """A comparison whose post sample is well outside the settle window.

    Named apart from the _cmp above rather than reusing it: that helper leaves
    the timestamps unset, and these cases must reach the ratio arithmetic
    instead of stopping at the settle-window guard.
    """
    return gates.BaselineComparison(
        node="monitor-0",
        metric=metric,
        baseline=baseline,
        post_settle=post,
        baseline_at_ms=0,
        post_settle_at_ms=gates.MIN_SETTLE_MS * 2,
    )


def test_a_tiny_baseline_is_unknown_not_a_failure() -> None:
    """Observed: monitor-0 finished at 7 UDP sockets against an idle 3.

    Reported FAIL at 2.33x on the box running the collector, which carried no
    test load at all. Four sockets is not a leak; the denominator was too small
    for a ratio to carry meaning, and at a baseline of 3 the smallest possible
    movement already exceeds the 1.10x tolerance.
    """
    result = gates.return_to_baseline_gates(
        [_settled_cmp("sockstat_udp_inuse", 3, 7)], tolerance=1.10
    )[0]
    assert result.status == gates.UNKNOWN
    assert "below" in result.detail


def test_a_real_denominator_still_fails_when_it_should() -> None:
    """The guard must not become a way to pass a genuine leak."""
    result = gates.return_to_baseline_gates(
        [_settled_cmp("filefd_allocated", 4096, 40960)], tolerance=1.10
    )[0]
    assert result.status == gates.FAIL


def test_a_real_denominator_still_passes_when_it_should() -> None:
    result = gates.return_to_baseline_gates(
        [_settled_cmp("filefd_allocated", 4096, 4100)], tolerance=1.10
    )[0]
    assert result.status == gates.PASS
