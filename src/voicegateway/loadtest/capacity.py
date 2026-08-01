"""Sizing a fleet from a measured calls-per-node figure.

Two numbers, and they must never be reconciled
-----------------------------------------------

:data:`SIZING_MARGIN` (0.85) and the acceptance gate's headroom floor (0.20)
look like they should add to 1.0. They do not, they measure different things,
and a future reader who "corrects" one to match the other breaks the sizing:

* **0.85** is a CPU margin applied when sizing, so that the nodes which SURVIVE
  a node loss are still under the ceiling. It answers "how many nodes do I buy".
* **0.20** is an acceptance threshold on remaining file descriptors, network and
  RTP ports, checked on a run that already happened. It answers "did this run
  leave room".

One is a purchasing decision, the other is a pass/fail on evidence.

The false ceiling
-----------------

A ramp finds the calls-per-node figure by pushing one node to saturation. That
only works if the GENERATOR can actually reach each step. By Little's law the
concurrency a generator sustains is ``rate x duration``, where duration is the
full INVITE-to-BYE wall time, so a plan holding the rate FIXED while raising the
target concurrency plateaus at ``rate x duration`` and every step above that is
physically unreachable.

The failure is quiet and it is severe. The run completes, the numbers look
plausible, and the plateau reads as the node's ceiling when it is the load
generator's. Sizing off it buys the wrong number of machines, and no gate
downstream can catch it because every measurement in the run is internally
consistent.

So :func:`derive_calls_per_node` refuses to return a figure from a plateaued
ramp. It is the one input to the whole capacity table, and a wrong one is worse
than a missing one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# The CPU margin used when SIZING, so surviving nodes stay under the ceiling
# after one is lost. See the module docstring: never reconcile this with the
# 0.20 headroom acceptance floor, which measures something else entirely.
SIZING_MARGIN = 0.85

# One spare node, at EVERY tier. The margin above keeps survivors under the
# ceiling; this is the node that is allowed to be lost in the first place.
SPARE_NODES = 1

# Full INVITE-to-BYE wall time is the hold plus call setup and teardown. A
# default, not a measurement: a caller with a measured duration should pass it.
DEFAULT_SETUP_OVERHEAD_S = 2.0

# How close two steps' peaks must be to count as the same plateau. Generous on
# purpose: this exists to catch a generator that stopped scaling, and real
# concurrency wobbles by a few calls between steps.
PLATEAU_TOLERANCE = 0.05


@dataclass(frozen=True)
class InstanceType:
    """A machine type, quoted from a source rather than derived.

    Nothing in this module can compute which instance type to buy: that is a
    decision recorded somewhere else, and the only honest thing to do with it is
    repeat it and say where it came from. ``citation`` is required for exactly
    that reason, so a table can footnote its provenance and a reader can check
    it against the source rather than trusting this code.
    """

    name: str
    role: str
    citation: str

    def __post_init__(self) -> None:
        if not self.citation.strip():
            raise ValueError(
                f"instance type {self.name!r} needs a citation: this module "
                "cannot derive a machine type, so one with no recorded source "
                "would be an invented recommendation"
            )


@dataclass(frozen=True)
class RampStep:
    """One step of a ramp: what was asked for, and what was reached."""

    # Optional because an imported row never carries it: the target lives in
    # the generator's scenario file, which is not an artifact. A step with no
    # target cannot be said to have asked for more, which is what the plateau
    # scan needs and why it skips them.
    target_concurrency: int | None
    peak_concurrency: int | None = None
    peak_cpu_utilisation: float | None = None
    # The generator's arrival rate and hold, when known. Used to predict a
    # plateau BEFORE a run rather than diagnosing one after.
    rate_per_second: float | None = None
    hold_seconds: float | None = None


@dataclass(frozen=True)
class PlateauFinding:
    """A ramp that stopped climbing, and why that invalidates its ceiling."""

    plateaued: bool
    detail: str
    # The concurrency the steps converged on, when they did.
    plateau_at: int | None = None
    # Steps whose target the run never came close to reaching.
    unreachable_targets: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class CapacityTier:
    """How many nodes one target concurrency needs."""

    target_concurrency: int
    calls_per_node: int
    usable_per_node: float
    nodes_for_load: int
    spare_nodes: int

    @property
    def nodes(self) -> int:
        return self.nodes_for_load + self.spare_nodes


def sustainable_concurrency(
    rate_per_second: float,
    hold_seconds: float,
    *,
    setup_overhead_s: float = DEFAULT_SETUP_OVERHEAD_S,
) -> float:
    """Little's law: the concurrency a fixed arrival rate can hold.

    Duration is the full INVITE-to-BYE wall time, hold plus setup, because a
    call occupies a slot from the moment it is placed and not from the moment it
    is answered.
    """
    if rate_per_second <= 0 or hold_seconds < 0:
        raise ValueError(
            f"rate {rate_per_second} and hold {hold_seconds} must be positive: "
            "a non-positive arrival rate holds no calls at all"
        )
    return rate_per_second * (hold_seconds + setup_overhead_s)


def required_rate(
    target_concurrency: int,
    hold_seconds: float,
    *,
    setup_overhead_s: float = DEFAULT_SETUP_OVERHEAD_S,
) -> float:
    """The arrival rate a step needs to actually reach its target.

    The inverse of :func:`sustainable_concurrency`, and the number a ramp plan
    has to scale per step. A plan that holds the rate fixed while raising the
    target is asking for concurrency it cannot generate.
    """
    if target_concurrency <= 0:
        raise ValueError("target concurrency must be positive")
    return target_concurrency / (hold_seconds + setup_overhead_s)


def unreachable_steps(
    steps: list[RampStep], *, setup_overhead_s: float = DEFAULT_SETUP_OVERHEAD_S
) -> list[tuple[RampStep, float, float]]:
    """Steps whose target exceeds what their own rate and hold can sustain.

    Predicts the plateau from the PLAN, before anything runs, for every step
    that declared a rate and a hold. Returns ``(step, sustainable, required)``
    so a caller can say how far short the plan falls and what rate would fix it.
    """
    out: list[tuple[RampStep, float, float]] = []
    for step in steps:
        # All three are needed to say a step asks for more than its own plan
        # can produce. A step missing any of them is not evidence either way,
        # so it is skipped rather than assumed reachable.
        target = step.target_concurrency
        if target is None or step.rate_per_second is None or step.hold_seconds is None:
            continue
        ceiling = sustainable_concurrency(
            step.rate_per_second, step.hold_seconds, setup_overhead_s=setup_overhead_s
        )
        if target > ceiling:
            out.append(
                (
                    step,
                    ceiling,
                    required_rate(
                        target,
                        step.hold_seconds,
                        setup_overhead_s=setup_overhead_s,
                    ),
                )
            )
    return out


def detect_plateau(
    steps: list[RampStep], *, setup_overhead_s: float = DEFAULT_SETUP_OVERHEAD_S
) -> PlateauFinding:
    """Whether a ramp stopped climbing before its targets did.

    Two independent signals, because either alone can be fooled:

    * **From the plan.** A step whose target exceeds ``rate x duration`` cannot
      be reached no matter how healthy the node is.
    * **From the result.** Successive steps that reached the same peak while
      asking for more are a plateau whether or not the plan explains it.

    The second catches a node that genuinely saturated as well as a generator
    that ran out, and this function does not try to tell those apart. It says
    the ceiling is not attributable, which is the honest claim: a plateau means
    something stopped scaling, and deciding which thing needs a look at the
    node's own CPU.
    """
    predicted = unreachable_steps(steps, setup_overhead_s=setup_overhead_s)
    if predicted:
        worst = min(ceiling for _, ceiling, _ in predicted)
        # Every entry in `predicted` carries a known target: unreachable_steps
        # skips the ones that do not, so this is list[int] by construction.
        targets = [
            step.target_concurrency
            for step, _, _ in predicted
            if step.target_concurrency is not None
        ]
        fixes = ", ".join(
            f"{step.target_concurrency} needs r >= {rate:.2f}/s"
            for step, _, rate in predicted
        )
        return PlateauFinding(
            plateaued=True,
            plateau_at=int(worst),
            unreachable_targets=targets,
            detail=(
                f"the plan cannot reach {targets}: at the declared rate and hold "
                f"the generator sustains about {worst:.0f} concurrent calls, so "
                f"those steps measure the generator and not the node. {fixes}"
            ),
        )

    # A step with no recorded target cannot be said to have asked for more, so
    # it cannot contribute to a plateau finding. Imported rows routinely have
    # none: the target lives in the generator's scenario file, which is not an
    # artifact, so this is the common case rather than the exceptional one.
    reached = [
        (s.target_concurrency, s.peak_concurrency)
        for s in steps
        if s.peak_concurrency is not None and s.target_concurrency is not None
    ]
    for (prev_target, prev_peak), (target, peak) in zip(
        reached, reached[1:], strict=False
    ):
        if target <= prev_target or prev_peak <= 0:
            continue
        # Asked for more, got the same. Whatever the cause, the higher step
        # demonstrates nothing the lower one did not.
        if abs(peak - prev_peak) <= PLATEAU_TOLERANCE * prev_peak:
            return PlateauFinding(
                plateaued=True,
                plateau_at=peak,
                unreachable_targets=[target],
                detail=(
                    f"the step asking for {target} reached {peak}, the same as "
                    f"the step asking for {prev_target} ({prev_peak}). Something "
                    "stopped scaling, and until it is known whether that was the "
                    "node or the generator this ceiling is not attributable"
                ),
            )

    return PlateauFinding(plateaued=False, detail="no plateau detected")


def derive_calls_per_node(
    steps: list[RampStep],
    *,
    cpu_ceiling: float,
    setup_overhead_s: float = DEFAULT_SETUP_OVERHEAD_S,
) -> tuple[int | None, str]:
    """The calls-per-node figure, or None with the reason it is not derivable.

    Returns the highest concurrency actually REACHED while the node stayed at or
    under ``cpu_ceiling``. Refuses in four cases, each of which would otherwise
    yield a plausible number:

    * The ramp plateaued. The ceiling belongs to whatever stopped scaling, and a
      generator's limit sized as a node's limit buys the wrong fleet.
    * No step reached the CPU ceiling. The node was never saturated, so the
      largest concurrency observed is a floor on its capacity, not a measure of
      it, and sizing from it would UNDER-provision.
    * Nothing measured CPU at all. An unscraped ramp demonstrates nothing.
    * No step declared what it asked for, so a plateau could not be ruled out.
      "No plateau detected" from a ramp that recorded neither targets nor rates
      means the check never ran, which is not the same as a ramp that climbed.
    """
    plateau = detect_plateau(steps, setup_overhead_s=setup_overhead_s)
    if plateau.plateaued:
        return None, plateau.detail

    # A plateau can only be RULED OUT when the steps say what they asked for.
    # With no targets and no declared rates, "no plateau detected" means the
    # check could not run, not that the ramp kept climbing, and a false ceiling
    # sized as a real one buys the wrong fleet. Refusing is the conservative
    # reading and the only honest one.
    declared = [
        s
        for s in steps
        if s.target_concurrency is not None
        or (s.rate_per_second is not None and s.hold_seconds is not None)
    ]
    if len(steps) > 1 and not declared:
        return None, (
            f"no step recorded a target concurrency or an arrival rate, so a "
            f"plateau could not be ruled out across {len(steps)} steps. The "
            "highest concurrency reached may be the load generator's ceiling "
            "rather than the node's, and sizing from it would be a guess"
        )

    # Narrowed into concrete pairs rather than filtered in place: a step is only
    # evidence when it carries BOTH halves, and pulling them out here means
    # nothing downstream has to re-assert that they are present.
    measured: list[tuple[int, float]] = []
    for step in steps:
        if step.peak_concurrency is None or step.peak_cpu_utilisation is None:
            continue
        measured.append((step.peak_concurrency, step.peak_cpu_utilisation))

    if not measured:
        return None, (
            "no step carried both a peak concurrency and a peak CPU reading, so "
            "nothing here shows what the node was doing at any concurrency"
        )

    under = [(calls, cpu) for calls, cpu in measured if cpu <= cpu_ceiling]
    if not under:
        lowest = min(cpu for _, cpu in measured)
        return None, (
            f"every step exceeded the {cpu_ceiling:.0%} CPU ceiling (lowest "
            f"observed {lowest:.1%}), so no concurrency here was sustained "
            "within it"
        )

    best = max(calls for calls, _ in under)
    if len(under) == len(measured):
        # The node never reached the ceiling, so it was never saturated.
        highest = max(cpu for _, cpu in measured)
        return None, (
            f"no step reached the {cpu_ceiling:.0%} CPU ceiling (peak observed "
            f"{highest:.1%}), so the node was never saturated. It carries AT "
            f"LEAST {best} calls, which is a floor on its capacity and not a "
            "measure of it: sizing from it would under-provision"
        )

    return best, (
        f"highest concurrency sustained at or under {cpu_ceiling:.0%} CPU across "
        f"{len(measured)} measured steps"
    )


def nodes_for(target_concurrency: int, calls_per_node: int) -> CapacityTier:
    """How many nodes one target needs, spare included.

    ``ceil(target / (margin x calls_per_node)) + spare``. The margin keeps the
    survivors under the CPU ceiling after a loss; the spare is the node that is
    allowed to be lost. Both apply at EVERY tier, not only the largest: a tier
    sized without the spare has no node to lose.
    """
    if calls_per_node <= 0:
        raise ValueError(
            f"calls_per_node must be positive, got {calls_per_node}: a node that "
            "carries no calls cannot be sized against"
        )
    if target_concurrency <= 0:
        raise ValueError("target concurrency must be positive")
    usable = SIZING_MARGIN * calls_per_node
    return CapacityTier(
        target_concurrency=target_concurrency,
        calls_per_node=calls_per_node,
        usable_per_node=usable,
        nodes_for_load=math.ceil(target_concurrency / usable),
        spare_nodes=SPARE_NODES,
    )


def capacity_table(
    calls_per_node: int, *, tiers: tuple[int, ...] = (100, 150, 300, 500)
) -> list[CapacityTier]:
    """The node count at each target concurrency."""
    return [nodes_for(target, calls_per_node) for target in tiers]


__all__ = [
    "DEFAULT_SETUP_OVERHEAD_S",
    "PLATEAU_TOLERANCE",
    "SIZING_MARGIN",
    "SPARE_NODES",
    "CapacityTier",
    "InstanceType",
    "PlateauFinding",
    "RampStep",
    "capacity_table",
    "derive_calls_per_node",
    "detect_plateau",
    "nodes_for",
    "required_rate",
    "sustainable_concurrency",
    "unreachable_steps",
]
