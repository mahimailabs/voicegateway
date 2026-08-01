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
