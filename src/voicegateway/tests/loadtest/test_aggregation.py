"""Correlating node samples to a test window, and the ways that reads wrong.

The sharp one is memory. Peak utilisation is where ``memory_available_bytes`` is
at its MINIMUM, and reaching for the maximum reports a machine at its emptiest as
its busiest. The fixtures below make the two answers differ by a wide margin so
the test cannot pass by coincidence.

Everything here is overlap, never attribution. Nothing asserts a node served a
call, because nothing server-side can.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from voicegateway.livekit_diag import gates
from voicegateway.loadtest import aggregation
from voicegateway.models.node_sample_model import NodeSample  # noqa: F401
from voicegateway.repository import node_correlation_repository as correlation
from voicegateway.repository.node_samples_repository import (
    NodeSampleInput,
    insert_samples,
)

T0 = 1_785_520_800_000  # 2026-07-31T18:00:00Z
SEC = 1000


@pytest.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'nodes.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(
            SQLModel.metadata.create_all,
            tables=[SQLModel.metadata.tables["node_samples"]],
        )
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


def _sample(offset_s: int, **values) -> NodeSampleInput:
    # values is a validated mapping, not kwargs: insert_samples RAISES on an
    # unknown key rather than dropping it, which is what keeps a typo'd column
    # from silently becoming a column nobody wrote.
    return NodeSampleInput(
        node="sfu-1",
        source="node_exporter",
        at_ms=T0 + offset_s * SEC,
        outcome="ok",
        values=values,
    )


async def _busy_cpu(db: AsyncSession) -> None:
    """A node whose CPU climbs to 75% busy.

    cpu_seconds_total accrues at the core count (4 cores here). Idle accrues at
    whatever is left over, so an idle rate of 1.0/s against a 4.0/s capacity is
    75% utilisation.
    """
    rows = [
        # (elapsed_s, cpu_total, cpu_idle)
        (0, 1000.0, 800.0),
        (10, 1040.0, 830.0),  # idle 3.0/s of 4.0/s -> 25% busy
        (20, 1080.0, 850.0),  # idle 2.0/s of 4.0/s -> 50% busy
        (30, 1120.0, 860.0),  # idle 1.0/s of 4.0/s -> 75% busy  <- peak
        (40, 1160.0, 890.0),  # idle 3.0/s of 4.0/s -> 25% busy
    ]
    await insert_samples(
        db,
        [
            _sample(s, cpu_seconds_total=total, cpu_idle_seconds_total=idle)
            for s, total, idle in rows
        ],
    )


# --------------------------------------------------------------------------
# CPU
# --------------------------------------------------------------------------


async def test_peak_cpu_comes_from_the_paired_counter_rates(db: AsyncSession) -> None:
    await _busy_cpu(db)
    agg = await aggregation.aggregate_test_window(
        db, started_at_ms=T0, ended_at_ms=T0 + 40 * SEC
    )
    assert agg is not None
    assert agg.peak_cpu_utilisation == pytest.approx(0.75)


async def test_a_counter_reset_does_not_read_as_an_idle_node(
    db: AsyncSession,
) -> None:
    """A reboot zeroes the counter. That instant is unknown, never 0% busy.

    Rendering a reset as idle is a clean bill of health at exactly the moment
    something restarted.
    """
    await insert_samples(
        db,
        [
            _sample(0, cpu_seconds_total=1000.0, cpu_idle_seconds_total=800.0),
            _sample(10, cpu_seconds_total=1040.0, cpu_idle_seconds_total=830.0),
            # Counters go backwards: the machine restarted.
            _sample(20, cpu_seconds_total=5.0, cpu_idle_seconds_total=4.0),
        ],
    )
    agg = await aggregation.aggregate_test_window(
        db, started_at_ms=T0, ended_at_ms=T0 + 20 * SEC
    )
    assert agg is not None
    # The one sourceable point is the 25% one; the reset contributes nothing.
    assert agg.peak_cpu_utilisation == pytest.approx(0.25)
    [reading] = agg.cpu_readings
    assert reading.samples == 1


async def test_an_unscrapeable_window_reads_none_with_a_reason(
    db: AsyncSession,
) -> None:
    """Not 0.0. No ceiling was demonstrated, which is not staying under one."""
    await insert_samples(
        db,
        [
            _sample(0, cpu_seconds_total=None, cpu_idle_seconds_total=None, rooms=3),
            _sample(10, cpu_seconds_total=None, cpu_idle_seconds_total=None, rooms=4),
        ],
    )
    agg = await aggregation.aggregate_test_window(
        db, started_at_ms=T0, ended_at_ms=T0 + 10 * SEC
    )
    assert agg is not None
    assert agg.peak_cpu_utilisation is None
    [reading] = agg.cpu_readings
    assert reading.utilisation is None
    assert reading.unmeasured_reason
    # And the gate turns that into UNKNOWN, never PASS.
    [gate] = gates.node_cpu_gates([reading])
    assert gate.status == gates.UNKNOWN


# --------------------------------------------------------------------------
# Memory: the min-vs-max trap
# --------------------------------------------------------------------------


async def test_peak_memory_is_where_available_is_lowest(db: AsyncSession) -> None:
    """The trap. Peak utilisation is the MINIMUM of memory_available_bytes.

    Total is 1000 throughout. Available bottoms out at 100, which is 90% used.
    Taking the maximum available (900) instead would report 10% used: a busy
    machine rendered as nearly empty.
    """
    await insert_samples(
        db,
        [
            _sample(0, memory_total_bytes=1000, memory_available_bytes=900),
            _sample(10, memory_total_bytes=1000, memory_available_bytes=400),
            _sample(20, memory_total_bytes=1000, memory_available_bytes=100),
            _sample(30, memory_total_bytes=1000, memory_available_bytes=600),
        ],
    )
    agg = await aggregation.aggregate_test_window(
        db, started_at_ms=T0, ended_at_ms=T0 + 30 * SEC
    )
    assert agg is not None
    assert agg.peak_memory_utilisation == pytest.approx(0.90)
    # The wrong answer, pinned: max(available)/total is 0.10 used.
    assert agg.peak_memory_utilisation != pytest.approx(0.10)


async def test_memory_is_paired_within_one_row(db: AsyncSession) -> None:
    """min(available) and max(total) from different rows is not a measurement.

    Here the row with the lowest available also has the smallest total, so a
    cross-row pairing would compute 1 - 100/2000 = 95% instead of the 50% that
    was actually true at that instant.
    """
    await insert_samples(
        db,
        [
            _sample(0, memory_total_bytes=2000, memory_available_bytes=1000),
            _sample(10, memory_total_bytes=200, memory_available_bytes=100),
        ],
    )
    agg = await aggregation.aggregate_test_window(
        db, started_at_ms=T0, ended_at_ms=T0 + 10 * SEC
    )
    assert agg is not None
    # Both rows are exactly 50% used.
    assert agg.peak_memory_utilisation == pytest.approx(0.50)
    assert agg.peak_memory_utilisation != pytest.approx(0.95)


async def test_a_row_missing_either_half_is_skipped_not_zeroed(
    db: AsyncSession,
) -> None:
    await insert_samples(
        db,
        [
            _sample(0, memory_total_bytes=1000, memory_available_bytes=None),
            _sample(10, memory_total_bytes=None, memory_available_bytes=500),
            _sample(20, memory_total_bytes=1000, memory_available_bytes=200),
        ],
    )
    agg = await aggregation.aggregate_test_window(
        db, started_at_ms=T0, ended_at_ms=T0 + 20 * SEC
    )
    assert agg is not None
    assert agg.peak_memory_utilisation == pytest.approx(0.80)
    [reading] = agg.memory_readings
    assert reading.samples == 1, "a half-measured row was counted as a reading"


async def test_memory_with_nothing_measured_is_none(db: AsyncSession) -> None:
    await insert_samples(db, [_sample(0, rooms=1), _sample(10, rooms=2)])
    agg = await aggregation.aggregate_test_window(
        db, started_at_ms=T0, ended_at_ms=T0 + 10 * SEC
    )
    assert agg is not None
    assert agg.peak_memory_utilisation is None
    [gate] = gates.node_memory_gates(agg.memory_readings)
    assert gate.status == gates.UNKNOWN


# --------------------------------------------------------------------------
# The worst node governs
# --------------------------------------------------------------------------


async def test_the_worst_node_governs_rather_than_the_average(
    db: AsyncSession,
) -> None:
    """One node breaching while three idle is a breach, not a healthy mean."""
    quiet = [
        NodeSampleInput(
            node=f"sfu-{n}",
            source="node_exporter",
            at_ms=T0 + s * SEC,
            outcome="ok",
            values={"memory_total_bytes": 1000, "memory_available_bytes": 900},
        )
        for n in (1, 2, 3)
        for s in (0, 10)
    ]
    loud = [
        NodeSampleInput(
            node="sfu-9",
            source="node_exporter",
            at_ms=T0 + s * SEC,
            outcome="ok",
            values={"memory_total_bytes": 1000, "memory_available_bytes": 50},
        )
        for s in (0, 10)
    ]
    await insert_samples(db, quiet + loud)
    agg = await aggregation.aggregate_test_window(
        db, started_at_ms=T0, ended_at_ms=T0 + 10 * SEC
    )
    assert agg is not None
    assert agg.nodes_seen == 4
    assert agg.peak_memory_utilisation == pytest.approx(0.95)
    # The average across nodes is 0.2375, which would have passed the 0.75
    # ceiling while one node sat at 95%.
    assert agg.peak_memory_utilisation > gates.MAX_NODE_MEMORY_UTILISATION


# --------------------------------------------------------------------------
# Windows
# --------------------------------------------------------------------------


async def test_a_test_with_no_window_correlates_to_nothing(db: AsyncSession) -> None:
    """Not an empty aggregate. There is no window to overlap.

    Inventing one from whichever end was recorded would attribute an arbitrary
    span of fleet activity to this test.
    """
    await _busy_cpu(db)
    assert (
        await aggregation.aggregate_test_window(
            db, started_at_ms=None, ended_at_ms=T0 + 40 * SEC
        )
        is None
    )
    assert (
        await aggregation.aggregate_test_window(db, started_at_ms=T0, ended_at_ms=None)
        is None
    )


async def test_samples_outside_the_padded_window_are_not_counted(
    db: AsyncSession,
) -> None:
    await _busy_cpu(db)
    # A window far after the samples, wider than the pad.
    agg = await aggregation.aggregate_test_window(
        db, started_at_ms=T0 + 3600 * SEC, ended_at_ms=T0 + 3700 * SEC
    )
    assert agg is not None
    assert agg.node_samples_in_window == 0
    assert agg.peak_cpu_utilisation is None


async def test_the_window_records_both_requested_and_padded_bounds(
    db: AsyncSession,
) -> None:
    """So a padded correlation can never be presented as an exact one."""
    await _busy_cpu(db)
    agg = await aggregation.aggregate_test_window(
        db, started_at_ms=T0, ended_at_ms=T0 + 40 * SEC, pad_ms=5000
    )
    assert agg is not None
    assert agg.window.requested_start_ms == T0
    assert agg.window.start_ms == T0 - 5000
    assert agg.window.pad_ms == 5000


async def test_the_sample_count_is_carried_so_a_peak_is_not_oversold(
    db: AsyncSession,
) -> None:
    await _busy_cpu(db)
    agg = await aggregation.aggregate_test_window(
        db, started_at_ms=T0, ended_at_ms=T0 + 40 * SEC
    )
    assert agg is not None
    assert agg.node_samples_in_window == 5


# --------------------------------------------------------------------------
# How a peak may be described
# --------------------------------------------------------------------------


def test_a_peak_below_the_percentile_floor_is_labelled_as_a_max() -> None:
    """ "max of 4" never "p95". Four points do not describe a distribution."""
    assert aggregation.peak_label(4) == "max of 4"
    assert aggregation.peak_label(gates.MIN_PERCENTILE_SAMPLES - 1).startswith("max of")
    assert aggregation.peak_label(gates.MIN_PERCENTILE_SAMPLES) == "p95"
    assert aggregation.peak_label(0) == "not_measured"


def test_the_percentile_floor_is_one_number_not_two() -> None:
    """gates and node_correlation_repository each define it.

    They agree today. This pins that, because a drift between them would let one
    surface call five samples a p95 while another calls it a max, and both would
    look right in isolation.
    """
    assert gates.MIN_PERCENTILE_SAMPLES == correlation.MIN_PERCENTILE_SAMPLES


# --------------------------------------------------------------------------
# The contract this rests on
# --------------------------------------------------------------------------


def test_a_gauge_is_refused_as_a_counter(db: AsyncSession) -> None:
    """The columns this module names must be of the kind it treats them as."""
    from voicegateway.repository.node_samples_repository import (
        COUNTER_COLUMNS,
        GAUGE_COLUMNS,
    )

    assert aggregation.CPU_CAPACITY_COLUMN in COUNTER_COLUMNS
    assert aggregation.CPU_IDLE_COLUMN in COUNTER_COLUMNS
    assert aggregation.MEMORY_AVAILABLE_COLUMN in GAUGE_COLUMNS
    assert aggregation.MEMORY_TOTAL_COLUMN in GAUGE_COLUMNS
    # Memory is never diffed, so asking for its rate is a caller bug.
    assert aggregation.MEMORY_AVAILABLE_COLUMN not in COUNTER_COLUMNS


# --------------------------------------------------------------------------
# Return to baseline: one sample must not decide a verdict
# --------------------------------------------------------------------------
#
# The settle side used to be the single newest row in the table. A report
# generated straight after a run (the documented flow) therefore read the scrape
# that had just caught the collector host running the import and the report
# generator, and failed that node for the reporting tool's own footprint. The
# same run regenerated an hour later passed, because by then the newest row was
# an ordinary one. A verdict that depends on when someone typed a command is not
# a verdict.
#
# The numbers below are a real 15-second trace of that: flat at 1312 to 1344
# descriptors and about 1.02 GB across eight minutes, with one sample carrying
# 1408 and 1.118 GB.

_TOLERANCE = 1.10

# (fd, memory_used_bytes) per 15s scrape after the run.
_SETTLE_TRACE = [
    (1344, 999_000_000),
    (1312, 1_040_000_000),
    (1408, 1_118_040_000),  # the report generator's own process, and nothing else
    (1312, 1_040_000_000),
    (1312, 1_027_000_000),
    (1344, 1_051_000_000),
    (1344, 1_008_000_000),
]
_BASELINE_FD = 1248
_BASELINE_MEM = 958_685_000
_MEM_TOTAL = 4_000_000_000
_RUN_S = 600  # ten minutes, so the settle check has real time to read

# The window is padded 15s on each side, so the run occupies
# [T0 - 15s, T0 + _RUN_S + 15s]. These offsets put the fixtures inside the two
# windows the gate reads: the 5 minutes before the run, and 5 to 10 minutes
# after it. Sample timing is part of what is under test here, not scaffolding:
# a settle sample taken a minute after teardown is not a post-settle reading.
_BASELINE_AT = -300  # seconds, first of eight at 15s -> -300 to -195
_SETTLE_AT = _RUN_S + 315  # 5m15s past the padded end, first of seven


def _under_load() -> list:
    """Samples DURING the run. A node is correlated to a test by having been
    sampled inside its window, so without these there is no node to compare and
    no gate at all. Their values do not feed this gate: it reads idle-before
    against settled-after, never the load itself."""
    return [
        _sample(
            i * 60,
            filefd_allocated=1400.0,
            memory_total_bytes=float(_MEM_TOTAL),
            memory_available_bytes=float(_MEM_TOTAL - 1_800_000_000),
        )
        for i in range(_RUN_S // 60 + 1)
    ]


async def _flat_baseline_then(db: AsyncSession, settle) -> None:
    """Flat idle before the run, then the given (fd, mem_used) trace after it."""
    rows = [
        _sample(
            _BASELINE_AT + i * 15,
            filefd_allocated=float(_BASELINE_FD),
            memory_total_bytes=float(_MEM_TOTAL),
            memory_available_bytes=float(_MEM_TOTAL - _BASELINE_MEM),
        )
        for i in range(8)
    ]
    rows += [
        _sample(
            _SETTLE_AT + i * 15,
            filefd_allocated=float(fd),
            memory_total_bytes=float(_MEM_TOTAL),
            memory_available_bytes=float(_MEM_TOTAL - used),
        )
        for i, (fd, used) in enumerate(settle)
    ]
    await insert_samples(db, rows + _under_load())


async def _baseline_gates(db: AsyncSession):
    agg = await aggregation.aggregate_test_window(
        db, started_at_ms=T0, ended_at_ms=T0 + _RUN_S * SEC
    )
    assert agg is not None
    return {
        g.subject: g
        for g in gates.return_to_baseline_gates(
            agg.baseline_comparisons, tolerance=_TOLERANCE
        )
    }


async def test_one_spike_in_a_flat_settle_window_is_not_a_failure(
    db: AsyncSession,
) -> None:
    """The production case: the reporting tool measuring itself.

    Under the old single-sample reading both of these were FAIL, at 1.13x and
    1.17x, from the one scrape that overlapped the report generator.
    """
    await _flat_baseline_then(db, _SETTLE_TRACE)
    results = await _baseline_gates(db)

    fd = results["sfu-1/filefd_allocated"]
    assert fd.status == gates.PASS, fd.detail
    assert fd.value == pytest.approx(1344 / _BASELINE_FD, rel=1e-3)

    mem = results["sfu-1/memory_used_bytes"]
    assert mem.status == gates.PASS, mem.detail
    assert mem.value == pytest.approx(1_040_000_000 / _BASELINE_MEM, rel=1e-3)


async def test_a_sustained_leak_still_fails(db: AsyncSession) -> None:
    """The median must not be a way to pass a real leak.

    Every settle sample is held up, not one, which is what a descriptor or
    memory leak looks like. The median moves with it.
    """
    leaked = [(1500, 1_400_000_000) for _ in _SETTLE_TRACE]
    await _flat_baseline_then(db, leaked)
    results = await _baseline_gates(db)

    assert results["sfu-1/filefd_allocated"].status == gates.FAIL
    assert results["sfu-1/memory_used_bytes"].status == gates.FAIL


async def test_a_leak_that_starts_midway_through_settle_still_fails(
    db: AsyncSession,
) -> None:
    """A median over N is not a majority vote that a leak has to win outright.

    Four of seven samples elevated is a resource that went up and stayed up, and
    it must read as one even though three earlier samples were clean.
    """
    climbing = [
        (1312, 1_000_000_000),
        (1312, 1_000_000_000),
        (1344, 1_020_000_000),
        (1600, 1_500_000_000),
        (1600, 1_500_000_000),
        (1600, 1_500_000_000),
        (1600, 1_500_000_000),
    ]
    await _flat_baseline_then(db, climbing)
    results = await _baseline_gates(db)

    assert results["sfu-1/filefd_allocated"].status == gates.FAIL
    assert results["sfu-1/memory_used_bytes"].status == gates.FAIL


async def test_a_spike_in_the_baseline_cannot_hide_a_leak(db: AsyncSession) -> None:
    """The dangerous direction, and why the baseline is a median too.

    A spike on the BEFORE side inflates the denominator, which drags the ratio
    down and turns a genuine leak into a clean bill of health. One reading of
    1600 among seven flat ones must not do that.

    The spike is on the LAST sample before the run deliberately: that is the one
    the single-sample reading took, so anywhere else in the window the test
    would pass against the old code and prove nothing.
    """
    rows = [
        _sample(
            _BASELINE_AT + i * 15,
            filefd_allocated=float(1600 if i == 7 else _BASELINE_FD),
            memory_total_bytes=float(_MEM_TOTAL),
            memory_available_bytes=float(_MEM_TOTAL - _BASELINE_MEM),
        )
        for i in range(8)
    ]
    rows += [
        _sample(
            _SETTLE_AT + i * 15,
            filefd_allocated=1500.0,
            memory_total_bytes=float(_MEM_TOTAL),
            memory_available_bytes=float(_MEM_TOTAL - _BASELINE_MEM),
        )
        for i in range(7)
    ]
    await insert_samples(db, rows + _under_load())
    results = await _baseline_gates(db)

    fd = results["sfu-1/filefd_allocated"]
    assert fd.status == gates.FAIL, fd.detail
    # 1500/1248, not 1500/1600: the spike did not become the baseline.
    assert fd.value == pytest.approx(1500 / _BASELINE_FD, rel=1e-3)


async def test_the_detail_says_what_the_number_is_a_median_of(
    db: AsyncSession,
) -> None:
    """A reader must not go hunting for a sample carrying the quoted value.

    "settled at 1344" with no qualifier sends someone looking for a scrape that
    read 1344 at a particular instant, which for a median need not exist at all.
    """
    await _flat_baseline_then(db, _SETTLE_TRACE)
    detail = (await _baseline_gates(db))["sfu-1/filefd_allocated"].detail
    assert "median of 7 samples" in detail
    assert "median of 8 samples" in detail


def test_a_single_sample_says_so_rather_than_calling_itself_a_median() -> None:
    """One reading either side is still allowed, and must be labelled as one."""
    [result] = gates.return_to_baseline_gates(
        [
            gates.BaselineComparison(
                node="sfu-1",
                metric="filefd_allocated",
                baseline=1000.0,
                post_settle=1010.0,
                baseline_at_ms=T0,
                post_settle_at_ms=T0 + 900_000,
                baseline_samples=1,
                post_settle_samples=1,
            )
        ],
        tolerance=_TOLERANCE,
    )
    assert result.status == gates.PASS
    assert "a single sample" in result.detail
    assert "median" not in result.detail


# --------------------------------------------------------------------------
# The baseline comes from the run, not from whatever the table still holds
# --------------------------------------------------------------------------
#
# The selection used to be "everything before the run", ascending, capped at
# 5,000 rows. At a 15s cadence that cap spans about 20.8 hours, so the rows it
# returned were the OLDEST retained history rather than anything near the run.
#
# The numbers below are a real hour-long 500-concurrent run. Its true ratio was
# marginal, 1.10x against a 1.10x tolerance. The old selection reported 0.51x,
# because it took its baseline from 21 hours earlier when the box was carrying
# 1.94 GB. A marginal FAIL rendered as a confident PASS.

_GB = 1_000_000_000
_TRUE_BASELINE = 0.7405 * _GB
_TRUE_SETTLE = 0.8147 * _GB
_STALE_BASELINE = 1.9362 * _GB  # 21 hours before the run, and irrelevant to it
_HOUR_S = 3600


async def test_an_old_sample_cannot_become_the_baseline(db: AsyncSession) -> None:
    """The regression, in one test: a true 1.10x must not report as 0.51x.

    The stale block is deliberately large. Under the old ascending-with-a-limit
    selection the oldest rows are exactly the ones that win, so a fixture with
    only a few of them would pass against the broken code and prove nothing.
    """
    stale = [
        _sample(
            -21 * _HOUR_S + i * 15,
            memory_total_bytes=float(_MEM_TOTAL),
            memory_available_bytes=float(_MEM_TOTAL - _STALE_BASELINE),
        )
        for i in range(240)  # an hour of it, 21 hours before the run
    ]
    near = [
        _sample(
            _BASELINE_AT + i * 15,
            memory_total_bytes=float(_MEM_TOTAL),
            memory_available_bytes=float(_MEM_TOTAL - _TRUE_BASELINE),
        )
        for i in range(20)
    ]
    settled = [
        _sample(
            _SETTLE_AT + i * 15,
            memory_total_bytes=float(_MEM_TOTAL),
            memory_available_bytes=float(_MEM_TOTAL - _TRUE_SETTLE),
        )
        for i in range(20)
    ]
    await insert_samples(db, stale + near + settled + _under_load())

    result = (await _baseline_gates(db))["sfu-1/memory_used_bytes"]
    assert result.value == pytest.approx(_TRUE_SETTLE / _TRUE_BASELINE, rel=1e-3)
    # What the old selection produced. Named so the failure message says which
    # bug came back rather than just quoting two floats.
    assert result.value != pytest.approx(_TRUE_SETTLE / _STALE_BASELINE, rel=1e-2), (
        "the baseline came from the stale block 21 hours before the run"
    )


async def test_retention_does_not_move_the_verdict(db: AsyncSession) -> None:
    """The coupling that made the retention setting a silent verdict-changer.

    Which rows survive is ``workers.node_sample_max_age_days``, and the old
    selection read the oldest of them, so changing retention moved every
    baseline on every re-rendered report with nothing connecting the two. The
    same run must now read the same, pruned or not.
    """
    near = [
        _sample(
            _BASELINE_AT + i * 15,
            memory_total_bytes=float(_MEM_TOTAL),
            memory_available_bytes=float(_MEM_TOTAL - _TRUE_BASELINE),
        )
        for i in range(20)
    ]
    settled = [
        _sample(
            _SETTLE_AT + i * 15,
            memory_total_bytes=float(_MEM_TOTAL),
            memory_available_bytes=float(_MEM_TOTAL - _TRUE_SETTLE),
        )
        for i in range(20)
    ]
    await insert_samples(db, near + settled + _under_load())
    pruned = (await _baseline_gates(db))["sfu-1/memory_used_bytes"]

    # Now the same run on a table that kept a day of older history.
    await insert_samples(
        db,
        [
            _sample(
                -21 * _HOUR_S + i * 15,
                memory_total_bytes=float(_MEM_TOTAL),
                memory_available_bytes=float(_MEM_TOTAL - _STALE_BASELINE),
            )
            for i in range(240)
        ],
    )
    retained = (await _baseline_gates(db))["sfu-1/memory_used_bytes"]

    assert retained.value == pytest.approx(pruned.value, rel=1e-9)
    assert retained.status == pruned.status


async def test_a_report_generated_before_the_settle_window_says_unknown(
    db: AsyncSession,
) -> None:
    """Not a PASS, and this is a behaviour change worth stating plainly.

    Running the report a minute after teardown used to produce a verdict from
    whatever the newest row happened to be. Nothing has settled a minute after
    teardown, so there is no return to report either way, and UNKNOWN is what
    ``MIN_SETTLE_MS`` always intended. It could not enforce it because it
    measured from the baseline sample rather than from the end of the run.
    """
    rows = [
        _sample(
            _BASELINE_AT + i * 15,
            memory_total_bytes=float(_MEM_TOTAL),
            memory_available_bytes=float(_MEM_TOTAL - _TRUE_BASELINE),
        )
        for i in range(20)
    ]
    rows += [
        _sample(
            _RUN_S + 60 + i * 15,  # one minute after teardown, still draining
            memory_total_bytes=float(_MEM_TOTAL),
            memory_available_bytes=float(_MEM_TOTAL - _TRUE_SETTLE),
        )
        for i in range(4)
    ]
    await insert_samples(db, rows + _under_load())

    result = (await _baseline_gates(db))["sfu-1/memory_used_bytes"]
    assert result.status == gates.UNKNOWN
    assert "settle window" in result.detail


async def test_the_detail_names_the_instant_its_baseline_came_from(
    db: AsyncSession,
) -> None:
    """A verdict nobody can check is not evidence.

    Two reports carried a 21-hour-old baseline without anyone noticing, because
    no artifact said which samples produced the numbers: verifying one meant
    querying the database.
    """
    await _flat_baseline_then(db, _SETTLE_TRACE)
    detail = (await _baseline_gates(db))["sfu-1/filefd_allocated"].detail

    from datetime import UTC, datetime

    expected = datetime.fromtimestamp(
        (T0 + (_BASELINE_AT + 7 * 15) * SEC) / 1000, tz=UTC
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert expected in detail, detail
