"""The collapsed health gates.

Two verdict implementations used to disagree (``service._verdict`` vs
``report.check_json``). Each disagreement is pinned here with the reading that
won, so a future edit that quietly relaxes one shows up as a failing test rather
than as a green CI run on a broken deployment.
"""

from __future__ import annotations

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

    steps = [RampStep(2, 11.0, 0.0, "Excellent"), RampStep(10, 14.0, 0.0, "Excellent")]
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
    no_rtt = gates.sfu_capacity_gate([{"clients": 2, "quality": "Excellent"}], 50.0, None)
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
