"""Per-test aggregation: the five columns a load-test report is owed.

Four of the five come from the generator's own artifacts and are already on the
row by the time this runs: concurrency, wall duration, the establishment counts,
and failures by cause. The fifth, peak CPU and memory per node, comes from
somewhere else entirely, and assembling it is what this module does.

Overlap, never attribution
--------------------------

Node samples are correlated to a test by TIME WINDOW OVERLAP. Nothing here says
a node served a call, because nothing server-side can: there is no per-call
identity at that layer, which is why ``node_samples`` carries no call id. Read
every number below as "this is what the fleet looked like while that test was
running".

Where each reading comes from
-----------------------------

CPU is a COUNTER pair. ``cpu_seconds_total`` is summed over every cpu and every
mode, so its rate IS the core count, and ``cpu_idle_seconds_total`` is the same
metric restricted to the idle mode. Utilisation is therefore ``1 - idle/total``
exactly, needing no core count and no assumption about machine size. The rates
come from :func:`counter_rates` via
:func:`node_samples_repository.utilisation_points`, so a counter reset (a reboot,
a restarted server) arrives as ``None`` and stays ``None`` rather than rendering
as a suddenly idle machine.

Memory is a GAUGE pair, and gauges are never diffed. Both values are read off the
SAME ROW, which is what makes them paired at one instant: taking
``min(available)`` from one sample and ``max(total)`` from another would compute
a utilisation that existed at no point in time.

**Peak memory utilisation corresponds to the MINIMUM of
``memory_available_bytes``, not its maximum.** Reaching for the maximum there is
the obvious mistake and it reports a machine at its emptiest as its busiest.

None is never zero
------------------

A node nobody could scrape reads ``None`` with a reason attached. An idle node
reads 0.0. Collapsing the first into the second is a clean bill of health nobody
earned, so every unmeasured path here carries ``unmeasured_reason`` and the gates
turn it into UNKNOWN rather than PASS.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from voicegateway.livekit_diag import gates
from voicegateway.livekit_diag.gates import (
    MIN_PERCENTILE_SAMPLES,
    BaselineComparison,
    HeadroomReading,
    HealthSeriesReading,
    LifecycleReading,
    NodeUtilisationReading,
    TrendReading,
)
from voicegateway.repository.node_correlation_repository import (
    DEFAULT_WINDOW_PAD_MS,
    NodeWindow,
    list_nodes_sampled_in_window,
    window_of,
)
from voicegateway.repository.node_samples_repository import (
    DEFAULT_READ_LIMIT,
    list_samples,
    read_counter_rate,
    utilisation_points,
)

# The counter pair CPU utilisation is derived from. Named here so the two are
# always read together: one without the other is not a utilisation.
CPU_CAPACITY_COLUMN = "cpu_seconds_total"
CPU_IDLE_COLUMN = "cpu_idle_seconds_total"

# The gauge pair memory utilisation is derived from, read off the same row.
MEMORY_AVAILABLE_COLUMN = "memory_available_bytes"
MEMORY_TOTAL_COLUMN = "memory_total_bytes"

# The file-descriptor pair headroom is judged against. These are the PER-PROCESS
# rlimit, not the host filefd pair, and the difference is not cosmetic: the host
# maximum on an observed box reads 9.223372036854776e18, which is effectively
# unbounded and does not fit a 64-bit column, while this pair reads a real
# 524287. The limit a service actually hits is its own, and the host figure
# cannot produce a ratio at all.
REDIS_UP_COLUMN = "redis_up"
HEALTH_OK_COLUMN = "health_ok"
FD_USED_COLUMN = "process_open_fds"
FD_LIMIT_COLUMN = "process_max_fds"


@dataclass(frozen=True)
class TestAggregate:
    """What the fleet looked like while one test ran.

    ``peak_cpu_utilisation`` and ``peak_memory_utilisation`` are the WORST node's
    peak, as fractions in 0..1. The worst node governs because a fleet is only as
    healthy as its most loaded member, and averaging would hide the one node that
    breached while the others idled.

    Both are ``None`` when nothing in the window produced a usable reading, which
    is a different fact from 0.0 and must render as not-measured.
    """

    window: NodeWindow
    peak_cpu_utilisation: float | None
    peak_memory_utilisation: float | None
    node_samples_in_window: int
    cpu_readings: list[NodeUtilisationReading] = field(default_factory=list)
    memory_readings: list[NodeUtilisationReading] = field(default_factory=list)
    # Redis reachability and health-endpoint results as ORDERED sample series,
    # one per node per source. Ordered because the criterion is about a RUN of
    # consecutive failures, which a peak or an average cannot express.
    health_readings: list[HealthSeriesReading] = field(default_factory=list)
    # Restarts, crashes and OOM kills per node per source.
    lifecycle_readings: list[LifecycleReading] = field(default_factory=list)
    # Resources that should be flat once the ramp is over.
    trend_readings: list[TrendReading] = field(default_factory=list)
    # Idle before against settled after, per node. Populated only when idle
    # samples exist on BOTH sides; a missing side is an UNKNOWN, not a pass.
    baseline_comparisons: list[BaselineComparison] = field(default_factory=list)
    # File-descriptor headroom per node, already in the shape the gate judges.
    # Carried here rather than rebuilt in the judge so the measurement lives in
    # one place and the judge stays a thing that only decides.
    fd_readings: list[HeadroomReading] = field(default_factory=list)

    @property
    def nodes_seen(self) -> int:
        """How many ``(node, source)`` targets had samples overlapping the window."""
        return len({(r.node, r.source) for r in self.cpu_readings})


def peak_label(samples: int) -> str:
    """How a peak over ``samples`` readings may be described.

    Below :data:`MIN_PERCENTILE_SAMPLES` a peak is a maximum over a handful of
    points and calling it a p95 would claim a distribution that was never
    observed. The threshold is imported rather than restated so there is one
    number, not two that can drift.
    """
    if samples <= 0:
        return "not_measured"
    if samples < MIN_PERCENTILE_SAMPLES:
        return f"max of {samples}"
    return "p95"


def _worst(readings: list[NodeUtilisationReading]) -> float | None:
    """The highest measured utilisation, or None when none was measured.

    ``max`` over an empty sequence is an error and ``max(..., default=0.0)``
    would be worse than an error: it would report a fully idle fleet for a window
    nobody scraped.
    """
    measured = [r.utilisation for r in readings if r.utilisation is not None]
    return max(measured) if measured else None


async def _cpu_reading(
    db: AsyncSession, *, node: str, source: str, window: NodeWindow, limit: int
) -> NodeUtilisationReading:
    """One node's peak CPU utilisation over the window."""
    capacity = await read_counter_rate(
        db,
        node=node,
        source=source,
        column=CPU_CAPACITY_COLUMN,
        since_ms=window.start_ms,
        until_ms=window.end_ms,
        limit=limit,
    )
    idle = await read_counter_rate(
        db,
        node=node,
        source=source,
        column=CPU_IDLE_COLUMN,
        since_ms=window.start_ms,
        until_ms=window.end_ms,
        limit=limit,
    )
    points = utilisation_points(capacity, idle)
    usable = [p.fraction for p in points if p.fraction is not None]
    if not usable:
        # Deliberately does not distinguish why. counter_rates is the one place
        # the reset rule lives, and it hands back None for a reset, a NULL, a
        # non-positive delta and the window's first point alike. Guessing
        # between them here would be a second copy of that rule.
        return NodeUtilisationReading(
            node=node,
            source=source,
            utilisation=None,
            samples=0,
            unmeasured_reason=(
                f"no usable {CPU_CAPACITY_COLUMN}/{CPU_IDLE_COLUMN} rate pair in "
                f"the window ({len(points)} points, none sourceable)"
            ),
        )
    return NodeUtilisationReading(
        node=node, source=source, utilisation=max(usable), samples=len(usable)
    )


async def _memory_reading(
    db: AsyncSession, *, node: str, source: str, window: NodeWindow, limit: int
) -> NodeUtilisationReading:
    """One node's peak memory utilisation over the window.

    Paired per ROW, so both halves describe the same instant. Peak utilisation
    is where AVAILABLE is at its minimum.
    """
    rows = await list_samples(
        db,
        node=node,
        source=source,
        since_ms=window.start_ms,
        until_ms=window.end_ms,
        limit=limit,
    )
    fractions: list[float] = []
    for row in rows:
        available = getattr(row, MEMORY_AVAILABLE_COLUMN)
        total = getattr(row, MEMORY_TOTAL_COLUMN)
        if available is None or total is None or total <= 0:
            # A NULL on either side is not measured, and a non-positive total
            # gives nothing to be a fraction of.
            continue
        used = float(total) - float(available)
        if used < 0:
            # More available than exists. The two did not come from the same
            # machine, so the number they imply is not a measurement.
            continue
        fractions.append(used / float(total))
    if not fractions:
        return NodeUtilisationReading(
            node=node,
            source=source,
            utilisation=None,
            samples=0,
            unmeasured_reason=(
                f"no row in the window carried both {MEMORY_AVAILABLE_COLUMN} "
                f"and {MEMORY_TOTAL_COLUMN} ({len(rows)} rows read)"
            ),
        )
    # The maximum FRACTION, which is the minimum available. Taking the maximum
    # of memory_available_bytes instead would report the emptiest moment as the
    # busiest one.
    return NodeUtilisationReading(
        node=node, source=source, utilisation=max(fractions), samples=len(fractions)
    )


#: What a leak looks like per metric. Occupancy climbs; FREE memory falls, and
#: reading that backwards would report a memory leak as healthy.
TREND_METRICS: tuple[tuple[str, bool], ...] = (
    ("sip_calls_active", True),
    ("rooms", True),
    ("participants", True),
    ("memory_available_bytes", False),
)

#: What must come back after teardown. Deliberately not heap or goroutines,
#: which the diagnostics probe already covers: these are the host-level
#: resources a load test exhausts.
#: Every one of these must be a metric where HIGHER IS WORSE, because
#: return_to_baseline compares post/baseline against a ceiling. Free memory is
#: the trap: it is higher-is-better, so comparing memory_available_bytes here
#: reports a node that ENDED WITH MORE FREE MEMORY as having failed to return.
#: Memory is therefore carried as USED, derived from the total/available pair.
BASELINE_METRICS: tuple[str, ...] = (
    "memory_used_bytes",
    "filefd_allocated",
    "sockstat_udp_inuse",
)


async def _lifecycle_reading(
    db: AsyncSession, *, node: str, source: str, window: NodeWindow, limit: int
) -> LifecycleReading:
    """Process start times and OOM counter for one subject, in time order."""
    rows = await list_samples(
        db,
        node=node,
        source=source,
        since_ms=window.start_ms,
        until_ms=window.end_ms,
        limit=limit,
    )
    return LifecycleReading(
        node=node,
        source=source,
        start_times=tuple(row.process_start_time_seconds for row in rows),
        oom_kills=tuple(row.vmstat_oom_kill for row in rows),
    )


async def _trend_readings(
    db: AsyncSession, *, node: str, source: str, window: NodeWindow, limit: int
) -> list[TrendReading]:
    """One reading per trendable metric this subject actually carried."""
    rows = await list_samples(
        db,
        node=node,
        source=source,
        since_ms=window.start_ms,
        until_ms=window.end_ms,
        limit=limit,
    )
    out: list[TrendReading] = []
    for metric, rising_is_bad in TREND_METRICS:
        values = tuple(getattr(row, metric) for row in rows)
        if all(v is None for v in values):
            continue
        out.append(
            TrendReading(
                node=node,
                source=source,
                metric=metric,
                values=values,
                rising_is_bad=rising_is_bad,
                window_ms=window.end_ms - window.start_ms,
            )
        )
    return out


async def _baseline_comparisons(
    db: AsyncSession, *, node: str, source: str, window: NodeWindow, limit: int
) -> list[BaselineComparison]:
    """Idle before the test against settled after it, for one node.

    Reads OUTSIDE the test window on purpose: the samples are all in one table,
    so the report can look either side without a second collection pass.

    A missing side yields an UNKNOWN comparison rather than being skipped. No
    pre-run idle sample means nobody established what baseline WAS, and a run
    that never measured its starting point cannot show it returned to it.
    """
    before = await list_samples(
        db, node=node, source=source, until_ms=window.start_ms - 1, limit=limit
    )
    after = await list_samples(
        db, node=node, source=source, since_ms=window.end_ms + 1, limit=limit
    )
    def _value(row, metric: str) -> float | None:
        # Derived, because there is no memory_used_bytes column and the raw
        # available figure runs the wrong way for this gate.
        if metric == "memory_used_bytes":
            total = row.memory_total_bytes
            available = row.memory_available_bytes
            if total is None or available is None:
                return None
            return float(total - available)
        value = getattr(row, metric)
        return None if value is None else float(value)

    out: list[BaselineComparison] = []
    for metric in BASELINE_METRICS:
        pre = [r for r in before if _value(r, metric) is not None]
        post = [r for r in after if _value(r, metric) is not None]
        if not pre and not post:
            continue
        reason = None
        if not pre:
            reason = (
                "not measured: no idle sample was recorded before the test, "
                "so no baseline was ever established to return to"
            )
        elif not post:
            reason = (
                "not measured: no sample was recorded after the test window, "
                "so nothing shows the resource came back"
            )
        out.append(
            BaselineComparison(
                node=node,
                source=source,
                metric=metric,
                baseline=_value(pre[-1], metric) if pre else None,
                post_settle=_value(post[-1], metric) if post else None,
                baseline_at_ms=pre[-1].at_ms if pre else None,
                post_settle_at_ms=post[-1].at_ms if post else None,
                unmeasured_reason=reason,
            )
        )
    return out


async def _health_readings(
    db: AsyncSession, *, node: str, source: str, window: NodeWindow, limit: int
) -> list[HealthSeriesReading]:
    """Redis and health-endpoint sample series for one node and source.

    Ordered by time, because "three consecutive failures" is a statement about
    sequence. A row whose column is NULL contributes None rather than being
    dropped: dropping it would splice two failures either side of an unsampled
    tick into a run that was never observed.

    Returns an empty list when the source produced no rows carrying the column
    at all, which the judge turns into an UNKNOWN naming what was not
    configured.
    """
    rows = await list_samples(
        db,
        node=node,
        source=source,
        since_ms=window.start_ms,
        until_ms=window.end_ms,
        limit=limit,
    )
    out: list[HealthSeriesReading] = []
    for column, subject in (
        (REDIS_UP_COLUMN, gates.HEALTH_SUBJECT_REDIS),
        (HEALTH_OK_COLUMN, gates.HEALTH_SUBJECT_ENDPOINT),
    ):
        values = tuple(getattr(row, column) for row in rows)
        if all(v is None for v in values):
            continue
        codes = (
            tuple(row.health_status_code for row in rows)
            if column == HEALTH_OK_COLUMN
            else ()
        )
        out.append(
            HealthSeriesReading(
                node=node, source=source, subject=subject, samples=values, codes=codes
            )
        )
    return out


async def _fd_reading(
    db: AsyncSession, *, node: str, source: str, window: NodeWindow, limit: int
) -> HeadroomReading:
    """One node's WORST file-descriptor headroom over the window.

    Worst, not last: headroom is a floor, and a run that touched 95% for one
    scrape and settled back has still demonstrated it can get there. The pair is
    read off the SAME ROW so both halves describe one instant, exactly as memory
    is, because an open count from one scrape against a limit from another
    describes a state that never existed.

    A row missing either half contributes nothing rather than a zero. When no
    row carries both, the reading is unmeasured WITH a reason, and the gate
    turns that into UNKNOWN rather than a pass.
    """
    rows = await list_samples(
        db,
        node=node,
        source=source,
        since_ms=window.start_ms,
        until_ms=window.end_ms,
        limit=limit,
    )
    worst: tuple[float, float] | None = None
    for row in rows:
        used = getattr(row, FD_USED_COLUMN)
        ceiling = getattr(row, FD_LIMIT_COLUMN)
        if used is None or ceiling is None or ceiling <= 0:
            continue
        if worst is None or (used / ceiling) > (worst[0] / worst[1]):
            worst = (float(used), float(ceiling))
    if worst is None:
        return HeadroomReading(
            node=node,
            resource="file_descriptors",
            used=None,
            limit=None,
            source=source,
            unmeasured_reason=(
                f"no row in the window carried both {FD_USED_COLUMN} and "
                f"{FD_LIMIT_COLUMN} ({len(rows)} rows read)"
            ),
        )
    return HeadroomReading(
        node=node,
        resource="file_descriptors",
        used=worst[0],
        limit=worst[1],
        source=source,
    )


async def aggregate_test_window(
    db: AsyncSession,
    *,
    started_at_ms: int | None,
    ended_at_ms: int | None,
    node: str | None = None,
    source: str | None = None,
    pad_ms: int = DEFAULT_WINDOW_PAD_MS,
    limit: int = DEFAULT_READ_LIMIT,
) -> TestAggregate | None:
    """Correlate node samples to one test's window.

    None when the test has no window to correlate against. A test whose start or
    end was never recorded cannot be correlated at all, and inventing a window
    from the other end would silently attribute an arbitrary span of fleet
    activity to it.
    """
    if started_at_ms is None or ended_at_ms is None:
        return None

    window = window_of(started_at_ms, ended_at_ms, pad_ms=pad_ms)
    targets = await list_nodes_sampled_in_window(
        db, window=window, node=node, source=source
    )

    cpu: list[NodeUtilisationReading] = []
    memory: list[NodeUtilisationReading] = []
    fds: list[HeadroomReading] = []
    health: list[HealthSeriesReading] = []
    lifecycle: list[LifecycleReading] = []
    trends: list[TrendReading] = []
    baselines: list[BaselineComparison] = []
    for target in targets:
        cpu.append(
            await _cpu_reading(
                db, node=target.node, source=target.source, window=window, limit=limit
            )
        )
        memory.append(
            await _memory_reading(
                db, node=target.node, source=target.source, window=window, limit=limit
            )
        )
        fds.append(
            await _fd_reading(
                db, node=target.node, source=target.source, window=window, limit=limit
            )
        )
        health.extend(
            await _health_readings(
                db, node=target.node, source=target.source, window=window, limit=limit
            )
        )
        lifecycle.append(
            await _lifecycle_reading(
                db, node=target.node, source=target.source, window=window, limit=limit
            )
        )
        trends.extend(
            await _trend_readings(
                db, node=target.node, source=target.source, window=window, limit=limit
            )
        )
        baselines.extend(
            await _baseline_comparisons(
                db, node=target.node, source=target.source, window=window, limit=limit
            )
        )

    return TestAggregate(
        window=window,
        peak_cpu_utilisation=_worst(cpu),
        peak_memory_utilisation=_worst(memory),
        # Every row overlapping the window, across targets. Carried so a peak
        # over three samples is never presented as if it came from three hundred.
        node_samples_in_window=sum(t.samples for t in targets),
        cpu_readings=cpu,
        memory_readings=memory,
        fd_readings=fds,
        health_readings=health,
        lifecycle_readings=lifecycle,
        trend_readings=trends,
        baseline_comparisons=baselines,
    )


__all__ = [
    "CPU_CAPACITY_COLUMN",
    "CPU_IDLE_COLUMN",
    "FD_LIMIT_COLUMN",
    "FD_USED_COLUMN",
    "MEMORY_AVAILABLE_COLUMN",
    "MEMORY_TOTAL_COLUMN",
    "TestAggregate",
    "aggregate_test_window",
    "peak_label",
]
