"""Fleet sizing, and the false ceiling that makes it buy the wrong number.

The centrepiece is the plateau. A ramp that holds its arrival rate fixed while
raising the target concurrency stops climbing at ``rate x duration``, and every
step above that measures the load generator rather than the node. The run
completes, every number in it is internally consistent, and the plateau reads as
the node's ceiling. Nothing downstream can catch it, so it is caught here.

The two margins are also pinned apart. 0.85 is a sizing decision; 0.20 is an
acceptance threshold on a run that already happened. They look like they should
sum to 1.0, and a reader who makes them do so breaks the sizing.
"""

from __future__ import annotations

import math

import pytest

from voicegateway.livekit_diag import gates
from voicegateway.loadtest import capacity
from voicegateway.loadtest.capacity import RampStep

# A ramp that holds the rate fixed while raising the target: 2 calls/s with a
# 60 s hold sustains about 124 concurrent, so the last two steps are asking for
# concurrency the generator cannot produce.
FIXED_RATE_RAMP = [
    RampStep(target_concurrency=n, rate_per_second=2.0, hold_seconds=60.0)
    for n in (25, 50, 100, 150, 200)
]


# --------------------------------------------------------------------------
# Little's law
# --------------------------------------------------------------------------


def test_sustainable_concurrency_counts_setup_not_just_hold() -> None:
    """A call occupies a slot from when it is placed, not from when answered."""
    assert capacity.sustainable_concurrency(2.0, 60.0) == pytest.approx(124.0)
    # Ignoring the setup overhead would say 120 and overstate what fits.
    assert capacity.sustainable_concurrency(2.0, 60.0, setup_overhead_s=0.0) == 120.0


def test_required_rate_is_the_inverse() -> None:
    for target, hold in ((150, 60.0), (500, 118.0), (100, 178.0)):
        rate = capacity.required_rate(target, hold)
        assert capacity.sustainable_concurrency(rate, hold) == pytest.approx(target)


def test_a_correctly_scaled_plan_matches_its_declared_rates() -> None:
    """The rates a well-formed plan declares are exactly target / duration."""
    assert capacity.required_rate(500, 118.0) == pytest.approx(4.1667, abs=1e-4)
    assert capacity.required_rate(300, 118.0) == pytest.approx(2.5)
    assert capacity.required_rate(150, 118.0) == pytest.approx(1.25)
    assert capacity.required_rate(100, 178.0) == pytest.approx(0.5556, abs=1e-4)


def test_a_non_positive_rate_is_refused() -> None:
    with pytest.raises(ValueError):
        capacity.sustainable_concurrency(0.0, 60.0)


# --------------------------------------------------------------------------
# The false ceiling
# --------------------------------------------------------------------------


def test_a_fixed_rate_ramp_is_caught_before_it_runs() -> None:
    """Predicted from the plan alone, with no results needed."""
    unreachable = capacity.unreachable_steps(FIXED_RATE_RAMP)
    assert [step.target_concurrency for step, _, _ in unreachable] == [150, 200]
    # And the fix is quantified, not just flagged.
    by_target = {step.target_concurrency: rate for step, _, rate in unreachable}
    assert by_target[150] == pytest.approx(2.419, abs=1e-3)
    assert by_target[200] == pytest.approx(3.226, abs=1e-3)


def test_the_reachable_steps_of_that_same_ramp_are_not_flagged() -> None:
    """Non-vacuous: the low steps are genuinely reachable at the same rate."""
    reachable = [s for s in FIXED_RATE_RAMP if s.target_concurrency <= 100]
    assert capacity.unreachable_steps(reachable) == []


def test_the_plateau_is_reported_with_the_concurrency_it_stalls_at() -> None:
    finding = capacity.detect_plateau(FIXED_RATE_RAMP)
    assert finding.plateaued is True
    assert finding.plateau_at == 124
    assert finding.unreachable_targets == [150, 200]
    assert "generator" in finding.detail


def test_a_plateau_is_also_caught_from_results_when_the_plan_is_silent() -> None:
    """A ramp that recorded no rates still betrays itself in what it reached."""
    observed = [
        RampStep(target_concurrency=100, peak_concurrency=100),
        RampStep(target_concurrency=150, peak_concurrency=124),
        RampStep(target_concurrency=200, peak_concurrency=125),
    ]
    finding = capacity.detect_plateau(observed)
    assert finding.plateaued is True
    assert finding.plateau_at == 125
    assert "not attributable" in finding.detail


def test_a_ramp_that_keeps_climbing_is_not_called_a_plateau() -> None:
    climbing = [
        RampStep(target_concurrency=100, peak_concurrency=100),
        RampStep(target_concurrency=150, peak_concurrency=149),
        RampStep(target_concurrency=200, peak_concurrency=198),
    ]
    assert capacity.detect_plateau(climbing).plateaued is False


# --------------------------------------------------------------------------
# Deriving the calls-per-node figure
# --------------------------------------------------------------------------


def test_a_plateaued_ramp_yields_no_figure_at_all() -> None:
    """The whole point. A generator's limit sized as a node's buys wrong.

    The plateau value is right there and looks like an answer, which is exactly
    why returning it would be the bug.
    """
    plateaued = [
        RampStep(
            target_concurrency=n,
            peak_concurrency=peak,
            peak_cpu_utilisation=cpu,
            rate_per_second=2.0,
            hold_seconds=60.0,
        )
        for n, peak, cpu in (
            (25, 25, 0.12),
            (50, 50, 0.24),
            (100, 100, 0.48),
            (150, 124, 0.58),
            (200, 124, 0.58),
        )
    ]
    value, reason = capacity.derive_calls_per_node(plateaued, cpu_ceiling=0.70)
    assert value is None
    assert "generator" in reason
    # The tempting wrong answer is sitting in the data.
    assert max(s.peak_concurrency for s in plateaued) == 124


def test_a_node_never_saturated_yields_a_floor_not_a_measure() -> None:
    """Sizing from an unsaturated node OVER-provisions.

    Every step stayed well under the ceiling, so the largest concurrency seen is
    a lower bound on capacity, not capacity. Treating that floor as the maximum
    divides the target by too small a number, so it buys MORE nodes than the run
    showed were needed. ``nodes_for`` demonstrates it: a 500-call target sized
    from a true 300/node needs 3 nodes, and sized from a 200/node floor needs 4.

    The assertion below read "under-provision" until 2026-08, matching the
    wording in the message it checked. Both were backwards, and the report told
    operators the opposite of what over-sizing does to their bill.
    """
    gentle = [
        RampStep(target_concurrency=n, peak_concurrency=n, peak_cpu_utilisation=cpu)
        for n, cpu in ((100, 0.20), (150, 0.30), (200, 0.41))
    ]
    value, reason = capacity.derive_calls_per_node(gentle, cpu_ceiling=0.70)
    assert value is None
    assert "AT LEAST 200" in reason
    assert "over-provisions" in reason
    assert "under-provision" not in reason


def test_a_properly_saturated_ramp_yields_the_highest_sustained_concurrency() -> None:
    saturated = [
        RampStep(target_concurrency=n, peak_concurrency=n, peak_cpu_utilisation=cpu)
        for n, cpu in ((100, 0.31), (150, 0.48), (200, 0.66), (250, 0.79))
    ]
    value, reason = capacity.derive_calls_per_node(saturated, cpu_ceiling=0.70)
    # 200 sat at 66%, under the ceiling; 250 breached it at 79%.
    assert value == 200
    assert "70%" in reason


def test_a_ramp_with_no_cpu_readings_yields_nothing() -> None:
    """An unscraped ramp demonstrates nothing about a node."""
    blind = [RampStep(target_concurrency=n, peak_concurrency=n) for n in (100, 200)]
    value, reason = capacity.derive_calls_per_node(blind, cpu_ceiling=0.70)
    assert value is None
    assert "nothing here shows" in reason


def test_a_ramp_that_breached_at_every_step_yields_nothing() -> None:
    hot = [
        RampStep(target_concurrency=n, peak_concurrency=n, peak_cpu_utilisation=cpu)
        for n, cpu in ((100, 0.81), (150, 0.93))
    ]
    value, reason = capacity.derive_calls_per_node(hot, cpu_ceiling=0.70)
    assert value is None
    assert "exceeded" in reason


def test_the_cpu_ceiling_used_is_the_gate_threshold() -> None:
    """The sizing ceiling and the acceptance ceiling are the same 70%.

    These two genuinely are one number, unlike the 0.85 and 0.20 pair below.
    """
    saturated = [
        RampStep(target_concurrency=n, peak_concurrency=n, peak_cpu_utilisation=cpu)
        for n, cpu in ((200, 0.69), (250, 0.71))
    ]
    value, _ = capacity.derive_calls_per_node(
        saturated, cpu_ceiling=gates.MAX_NODE_CPU_UTILISATION
    )
    assert value == 200


# --------------------------------------------------------------------------
# The table
# --------------------------------------------------------------------------


def test_the_spare_node_applies_at_every_tier_not_just_the_largest() -> None:
    """A tier sized without the spare has no node it can afford to lose."""
    table = capacity.capacity_table(150)
    assert [t.target_concurrency for t in table] == [100, 150, 300, 500]
    for tier in table:
        assert tier.spare_nodes == 1
        assert tier.nodes == tier.nodes_for_load + 1


def test_the_headline_tier_matches_the_formula_by_hand() -> None:
    """500 at C=150: ceil(500 / (0.85 x 150)) + 1 = ceil(3.92) + 1 = 5."""
    [tier] = capacity.capacity_table(150, tiers=(500,))
    assert tier.usable_per_node == pytest.approx(127.5)
    assert tier.nodes_for_load == 4
    assert tier.nodes == 5


@pytest.mark.parametrize(
    ("calls_per_node", "target", "expected"),
    [
        (150, 500, 5),
        (200, 500, 4),
        (124, 500, 6),  # what a plateaued ramp would have bought
        (150, 100, 2),
        (150, 300, 4),
    ],
)
def test_node_counts_across_the_table(
    calls_per_node: int, target: int, expected: int
) -> None:
    assert capacity.nodes_for(target, calls_per_node).nodes == expected


def test_the_false_ceiling_changes_what_you_buy() -> None:
    """Why the plateau guard is not pedantry.

    A ramp plateauing at 124 sizes 500 concurrent at six nodes. A node that
    actually carries 200 needs four. Two machines, bought on a number that
    described the load generator.
    """
    assert capacity.nodes_for(500, 124).nodes == 6
    assert capacity.nodes_for(500, 200).nodes == 4


def test_a_node_that_carries_nothing_is_refused_rather_than_dividing_by_zero() -> None:
    with pytest.raises(ValueError):
        capacity.nodes_for(500, 0)


def test_the_sizing_margin_and_the_headroom_floor_are_not_complements() -> None:
    """They look like they sum to 1.0. Making them do so breaks the sizing.

    0.85 is a CPU margin used when deciding how many nodes to buy. 0.20 is an
    acceptance threshold on remaining file descriptors and ports, checked
    against a run that already happened. Different quantities, different
    questions, and this test exists so nobody "corrects" one to match the other.
    """
    assert capacity.SIZING_MARGIN == 0.85
    assert gates.MIN_HEADROOM_FRACTION == 0.20
    assert capacity.SIZING_MARGIN + gates.MIN_HEADROOM_FRACTION != 1.0


# --------------------------------------------------------------------------
# Instance types are quoted, never derived
# --------------------------------------------------------------------------


def test_an_instance_type_without_a_source_is_refused() -> None:
    """Nothing here can compute a machine type, so an uncited one is invented."""
    with pytest.raises(ValueError):
        capacity.InstanceType(name="c7i.2xlarge", role="SIP", citation="  ")


def test_a_cited_instance_type_carries_its_source_through() -> None:
    quoted = capacity.InstanceType(
        name="c7i.2xlarge", role="SIP", citation="sizing-runbook.md:115"
    )
    assert quoted.citation == "sizing-runbook.md:115"


def test_the_node_count_is_a_ceiling_never_a_rounding() -> None:
    """3.1 nodes of load is four machines. Rounding down under-provisions."""
    tier = capacity.nodes_for(400, 150)
    assert tier.nodes_for_load == math.ceil(400 / 127.5) == 4


# --------------------------------------------------------------------------
# Imported rows carry no target concurrency, which is the common case
# --------------------------------------------------------------------------


def test_a_ramp_with_no_targets_does_not_crash_the_plateau_scan() -> None:
    """load_run_tests rows routinely have target_concurrency NULL.

    The target lives in the generator's scenario file, which is not an
    artifact, so nothing imports it. Comparing two unknown targets raised a
    TypeError and took the whole report command down the moment anything
    called the derivation.
    """
    steps = [
        RampStep(target_concurrency=None, peak_concurrency=p, peak_cpu_utilisation=c)
        for p, c in ((100, 0.31), (150, 0.48), (200, 0.66))
    ]
    finding = capacity.detect_plateau(steps)
    assert finding.plateaued is False


def test_unknown_targets_cannot_clear_a_ramp_of_plateauing() -> None:
    """ "No plateau detected" must not mean "the check could not run".

    With neither targets nor rates there is nothing to say a step asked for
    more, so a plateau cannot be ruled out. The highest concurrency reached may
    be the generator's ceiling, and sizing from it would be a guess.
    """
    steps = [
        RampStep(target_concurrency=None, peak_concurrency=p, peak_cpu_utilisation=c)
        for p, c in ((100, 0.31), (150, 0.48), (200, 0.66))
    ]
    value, reason = capacity.derive_calls_per_node(steps, cpu_ceiling=0.70)
    assert value is None
    assert "could not be ruled out" in reason
    # The tempting wrong answer is right there.
    assert max(s.peak_concurrency for s in steps) == 200


def test_one_step_is_not_asked_to_rule_out_a_plateau() -> None:
    """A single step cannot plateau against anything, so the guard does not fire.

    It still refuses, but for the honest reason: one step with no CPU reading
    shows nothing about the node.
    """
    [step] = [RampStep(target_concurrency=None, peak_concurrency=100)]
    value, reason = capacity.derive_calls_per_node([step], cpu_ceiling=0.70)
    assert value is None
    assert "could not be ruled out" not in reason


def test_a_declared_target_still_lets_the_derivation_answer() -> None:
    """Non-vacuous: the guard blocks unknown ramps, not every ramp."""
    steps = [
        RampStep(target_concurrency=t, peak_concurrency=p, peak_cpu_utilisation=c)
        for t, p, c in ((100, 100, 0.31), (200, 200, 0.66), (250, 250, 0.79))
    ]
    value, _ = capacity.derive_calls_per_node(steps, cpu_ceiling=0.70)
    assert value == 200


# --------------------------------------------------------------------------
# The setup-rate ceiling: a short step measures arrivals, not occupancy
# --------------------------------------------------------------------------
#
# The second way to size a fleet from the wrong number, and unlike the plateau
# it produces a figure that is too SMALL, so it over-provisions and nobody is
# ever paged about it. An observed run recorded 25 concurrent at 83.8% CPU over
# a 110-second ramp step and 100 concurrent at 66.5% on the same single node
# over a soak. Sized from the ramp, 100 calls needs nine nodes; the soak on the
# same page shows one node doing it.


#: The observed run, with the ramp steps that made it wrong.
_OBSERVED = [
    RampStep(
        target_concurrency=t,
        peak_concurrency=p,
        peak_cpu_utilisation=cpu,
        duration_seconds=secs,
    )
    for t, p, cpu, secs in (
        (25, 25, 0.838, 110.0),
        (30, 30, 0.712, 84.0),
        (100, 100, 0.665, 600.0),
    )
]


def test_a_setup_dominated_step_cannot_contribute_the_figure() -> None:
    """A short step must not supply the saturation a figure rests on.

    Without the filter the ramp steps breach the ceiling, the soak sits under
    it, and the derivation reads that as "saturated above 100, sustained at
    100" and returns 100 as a MEASURE. But the only step that held a population
    is the soak, and it never came near the ceiling, so 100 is a floor on this
    node's capacity and not a measure of it. The saturation was a call-setup
    rate, borrowed from steps measuring something else.

    Non-vacuous below: the unfiltered derivation really does answer.
    """
    assert (
        capacity.derive_calls_per_node(
            _OBSERVED, cpu_ceiling=0.70, min_steady_state_s=0.0
        )[0]
        == 100
    )
    value, reason = capacity.derive_calls_per_node(_OBSERVED, cpu_ceiling=0.70)
    assert value is None
    assert "AT LEAST 100" in reason, reason
    assert "setup-dominated" in reason


def test_the_excluded_steps_are_counted_in_the_reason() -> None:
    """A silent exclusion is a derivation that quietly used different data."""
    _value, reason = capacity.derive_calls_per_node(_OBSERVED, cpu_ceiling=0.70)
    assert "2 step(s) shorter than 300s" in reason


def test_every_step_too_short_refuses_and_says_what_to_change() -> None:
    """The common shape: a ramp of one-minute steps and nothing else.

    It used to yield a plausible figure, which is the worst of the three
    possible outcomes.
    """
    short = [
        RampStep(
            target_concurrency=n,
            peak_concurrency=n,
            peak_cpu_utilisation=cpu,
            duration_seconds=110.0,
        )
        for n, cpu in ((15, 0.59), (25, 0.84))
    ]
    value, reason = capacity.derive_calls_per_node(short, cpu_ceiling=0.70)
    assert value is None
    assert "longest 110s" in reason
    assert "over-provisions" in reason
    # The tempting wrong answer is right there, and it is exactly what shipped:
    # 15 calls per node sizes 100 concurrent at 9 nodes.
    assert (
        capacity.derive_calls_per_node(short, cpu_ceiling=0.70, min_steady_state_s=0.0)[
            0
        ]
        == 15
    )
    assert capacity.nodes_for(100, 15).nodes == 9


def test_an_unrecorded_duration_never_excludes_a_step() -> None:
    """Not knowing how long a step ran is not evidence that it was short.

    Excluding on a missing field would turn an unpopulated column into a missing
    capacity table, so it is kept and the reason says the check could not run.
    """
    steps = [
        RampStep(target_concurrency=n, peak_concurrency=n, peak_cpu_utilisation=cpu)
        for n, cpu in ((100, 0.31), (200, 0.66), (250, 0.79))
    ]
    value, reason = capacity.derive_calls_per_node(steps, cpu_ceiling=0.70)
    assert value == 200
    assert "recorded no duration" in reason


def test_a_long_enough_ramp_still_answers() -> None:
    """Non-vacuous: the floor blocks short steps, not every step."""
    steps = [
        RampStep(
            target_concurrency=n,
            peak_concurrency=n,
            peak_cpu_utilisation=cpu,
            duration_seconds=capacity.MIN_STEADY_STATE_S,
        )
        for n, cpu in ((100, 0.31), (200, 0.66), (250, 0.79))
    ]
    value, reason = capacity.derive_calls_per_node(steps, cpu_ceiling=0.70)
    assert value == 200
    assert "setup-dominated" not in reason
    assert "recorded no duration" not in reason


def test_the_floor_is_inclusive() -> None:
    """A step landing exactly on it has run long enough."""
    on = RampStep(
        target_concurrency=100,
        peak_concurrency=100,
        peak_cpu_utilisation=0.5,
        duration_seconds=capacity.MIN_STEADY_STATE_S,
    )
    under = capacity.RampStep(
        target_concurrency=100,
        peak_concurrency=100,
        peak_cpu_utilisation=0.5,
        duration_seconds=capacity.MIN_STEADY_STATE_S - 0.1,
    )
    eligible, too_short, _ = capacity._steady_state_steps(
        [on, under], capacity.MIN_STEADY_STATE_S
    )
    assert eligible == [on]
    assert too_short == [under]


def test_the_duration_filter_runs_before_the_plateau_scan() -> None:
    """A plateau across setup-dominated steps is a fact about setup rates.

    Reporting it as the reason sends an operator to fix their generator when the
    ramp shape was never the problem.
    """
    steps = [
        RampStep(
            target_concurrency=n,
            peak_concurrency=124,
            peak_cpu_utilisation=0.58,
            duration_seconds=60.0,
        )
        for n in (150, 200)
    ]
    _value, reason = capacity.derive_calls_per_node(steps, cpu_ceiling=0.70)
    assert "ran for under 300s" in reason
    assert "stopped scaling" not in reason


# --- parallel generators are not a ramp -------------------------------------


def _parallel_generators(
    count: int = 12,
    *,
    per_process_peak: int = 51,
    cpu: float = 0.5465968586387453,
    samples: int = 1374,
) -> list[RampStep]:
    """Rows shaped like a real churn run: N generators, one shared window.

    Every field that varies between genuine ramp steps is identical here, which
    is the point. The processes all watched the same fleet for the same 20
    minutes, so they report the same peak CPU from the same sample count, and
    only ``peak_concurrency`` is theirs alone.
    """
    return [
        RampStep(
            target_concurrency=per_process_peak,
            peak_concurrency=per_process_peak,
            peak_cpu_utilisation=cpu,
            duration_seconds=1200.0,
            samples_in_window=samples,
        )
        for _ in range(count)
    ]


def test_parallel_generators_are_detected_as_one_window() -> None:
    steps = _parallel_generators()
    assert capacity.concurrent_generators(steps) == 12


def test_a_real_ramp_is_not_mistaken_for_parallel_generators() -> None:
    """Sequential steps see different windows, so they must still derive.

    The detector keys on the pair, so a ramp whose steps happen to share a CPU
    reading is safe as long as their sample counts differ, which for genuinely
    separate stretches of time they do.
    """
    ramp = [
        RampStep(
            target_concurrency=n,
            peak_concurrency=n,
            peak_cpu_utilisation=cpu,
            duration_seconds=600.0,
            samples_in_window=samples,
        )
        for n, cpu, samples in (
            (100, 0.31, 300),
            (150, 0.48, 305),
            (200, 0.66, 298),
            (250, 0.79, 301),
        )
    ]
    assert capacity.concurrent_generators(ramp) == 0
    value, _reason = capacity.derive_calls_per_node(ramp, cpu_ceiling=0.70)
    assert value == 200


def test_parallel_generators_do_not_yield_a_per_node_capacity() -> None:
    """The regression this exists for.

    Twelve generator processes each peaked at 51 while the fleet carried 612
    across four SIP nodes, which is 153 per node. The report used to answer
    "it carries AT LEAST 51 calls", a per-generator number in a sentence about
    a node. An operator sizing from 51 buys three times the nodes the run
    showed were needed.
    """
    value, reason = capacity.derive_calls_per_node(
        _parallel_generators(), cpu_ceiling=0.70
    )

    assert value is None, f"a per-node capacity was derived from 12 generators: {value}"
    assert "51" not in reason.replace("612", ""), (
        "the per-generator concurrency is still quoted in the refusal, which is "
        f"how it was read as a per-node figure: {reason}"
    )
    assert "AT LEAST" not in reason
    # It must say WHY, and name the missing input rather than implying the run
    # was fine.
    assert "parallel" in reason
    assert "612" in reason, "the fleet total is what a reader needs to size from"
    assert "node" in reason


def test_the_refusal_names_the_missing_divisor() -> None:
    """A refusal that does not say what would fix it just looks like a bug."""
    _value, reason = capacity.derive_calls_per_node(
        _parallel_generators(), cpu_ceiling=0.70
    )
    assert "node count" in reason or "number of nodes" in reason


def test_one_generator_is_not_treated_as_parallel() -> None:
    """A single row shares a window with nothing, so the ramp path still runs."""
    single = _parallel_generators(count=1)
    assert capacity.concurrent_generators(single) == 0
    value, reason = capacity.derive_calls_per_node(single, cpu_ceiling=0.70)
    # Unsaturated, so it still refuses, but by the floor path rather than this
    # one: the two refusals are different facts.
    assert value is None
    assert "parallel" not in reason


def test_steps_missing_the_fingerprint_are_not_guessed_at() -> None:
    """No sample count means the question cannot be answered, so it is not.

    Older artifacts carry no window fingerprint. Treating absence as "not
    parallel" is the existing behaviour and is the safe direction here: it
    leaves those runs exactly as they were rather than refusing retroactively.
    """
    unfingerprinted = [
        RampStep(
            target_concurrency=51,
            peak_concurrency=51,
            peak_cpu_utilisation=0.54,
            duration_seconds=1200.0,
        )
        for _ in range(12)
    ]
    assert capacity.concurrent_generators(unfingerprinted) == 0
