"""``node_samples``: append, read-time counter diffing, and the trim.

The centrepiece is the counter-reset behaviour. A livekit-server or host restart
zeroes every ``_total``; the rate across that boundary must come back as
``None`` -- not 0 (which renders as an idle node at exactly the moment something
crashed) and not the raw negative (which renders as a spike pointing the wrong
way).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from voicegateway.models.node_sample_model import (  # noqa: F401 - registers table
    NodeSample,
)
from voicegateway.repository import node_samples_repository as repo


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    session = AsyncSession(engine)
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


def _sample(at_ms: int, **values: float | None) -> repo.NodeSampleInput:
    return repo.NodeSampleInput(
        node="sfu-1",
        source="livekit-server",
        at_ms=at_ms,
        outcome="ok",
        series_found=len(values),
        values=values,
    )


# ---------------------------------------------------------------------------
# The counter-reset rule
# ---------------------------------------------------------------------------


def test_counter_rates_first_point_has_no_rate() -> None:
    rates = repo.counter_rates([(0, 100.0)])
    assert rates[0].per_second is None


def test_counter_rates_computes_per_second() -> None:
    rates = repo.counter_rates([(0, 100.0), (15_000, 250.0)])
    assert rates[1].per_second == pytest.approx(10.0)


def test_counter_reset_yields_none_not_zero_and_not_negative() -> None:
    """A livekit-server restart zeroes every _total. The rate is UNKNOWN.

    The increment across the restart cannot be recovered: the counter may have
    climbed for most of the interval before it went to zero. Anything other than
    None is a fabricated reading.
    """
    points = [(0, 100.0), (15_000, 250.0), (30_000, 5.0), (45_000, 60.0)]
    rates = repo.counter_rates(points)

    restart = rates[2]
    assert restart.per_second is None, "a counter reset must not produce a number"
    assert restart.per_second != 0, "0 renders as an idle node, i.e. a clean bill"
    # And nothing anywhere in the series is negative.
    assert all(r.per_second is None or r.per_second >= 0 for r in rates)
    # The sample AFTER the restart diffs against the post-restart value, so the
    # series recovers on its own.
    assert rates[3].per_second == pytest.approx((60.0 - 5.0) / 15.0)


def test_an_unchanged_counter_is_a_real_zero_not_a_reset() -> None:
    """current == previous is not a decrease: the node genuinely idled."""
    rates = repo.counter_rates([(0, 100.0), (15_000, 100.0)])
    assert rates[1].per_second == 0.0


def test_a_null_value_breaks_the_chain_on_both_sides() -> None:
    """A missing scrape makes the increment unknown in both directions."""
    rates = repo.counter_rates([(0, 100.0), (15_000, None), (30_000, 400.0)])
    assert [r.per_second for r in rates] == [None, None, None]


def test_a_non_positive_time_delta_yields_none() -> None:
    """Duplicate timestamps or a clock step back: the division is meaningless."""
    rates = repo.counter_rates([(15_000, 100.0), (15_000, 200.0), (0, 300.0)])
    assert [r.per_second for r in rates] == [None, None, None]


async def test_read_counter_rate_emits_none_across_a_simulated_restart(
    db: AsyncSession,
) -> None:
    """The same rule, end to end through the table."""
    for at_ms, packets in ((0, 1_000), (15_000, 4_000), (30_000, 12), (45_000, 3_012)):
        await repo.insert_samples(db, [_sample(at_ms, packets_total=float(packets))])

    rates = await repo.read_counter_rate(
        db, node="sfu-1", source="livekit-server", column="packets_total"
    )
    assert [r.at_ms for r in rates] == [0, 15_000, 30_000, 45_000]
    assert rates[0].per_second is None  # nothing to diff against
    assert rates[1].per_second == pytest.approx(200.0)
    assert rates[2].per_second is None  # the restart
    assert rates[2].per_second != 0
    assert rates[3].per_second == pytest.approx(200.0)


async def test_read_counter_rate_refuses_a_gauge(db: AsyncSession) -> None:
    """The rate of change of filefd_allocated is not what anyone means by it."""
    with pytest.raises(ValueError, match="not a node_samples counter"):
        await repo.read_counter_rate(
            db, node="sfu-1", source="node-exporter", column="filefd_allocated"
        )


async def test_read_counter_rate_refuses_an_unknown_column(db: AsyncSession) -> None:
    """The whitelist is what keeps a column name out of the SQL text."""
    with pytest.raises(ValueError):
        await repo.read_counter_rate(
            db, node="sfu-1", source="node-exporter", column="1; DROP TABLE calls"
        )


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------


async def test_insert_then_read_round_trips(db: AsyncSession) -> None:
    await repo.insert_samples(
        db,
        [
            repo.NodeSampleInput(
                node="sfu-1",
                source="node-exporter",
                at_ms=1_700_000_000_000,
                outcome="ok",
                series_found=3,
                values={
                    "filefd_allocated": 1216.0,
                    "filefd_maximum": 9223372036854775807.0,
                    "load1": 0.42,
                },
            )
        ],
    )
    rows = await repo.list_samples(db, node="sfu-1", source="node-exporter")
    assert len(rows) == 1
    assert rows[0].filefd_allocated == 1216
    assert rows[0].load1 == pytest.approx(0.42)
    assert rows[0].outcome == "ok"
    assert rows[0].project == "default"


async def test_samples_append_they_do_not_upsert(db: AsyncSession) -> None:
    """Two scrapes of one node are two facts, not one row overwritten."""
    await repo.insert_samples(db, [_sample(0, rooms=1.0), _sample(15_000, rooms=2.0)])
    rows = await repo.list_samples(db, node="sfu-1", source="livekit-server")
    assert [r.rooms for r in rows] == [1, 2]


async def test_absent_series_stay_null_never_zero(db: AsyncSession) -> None:
    """A column nobody supplied is NULL: "not measured", not "measured as 0"."""
    await repo.insert_samples(db, [_sample(0, rooms=4.0)])
    row = (await repo.list_samples(db, node="sfu-1", source="livekit-server"))[0]
    assert row.rooms == 4
    assert row.packets_total is None
    assert row.packet_bytes_total is None
    assert row.nacks_total is None


async def test_a_failed_scrape_is_a_row_with_no_values(db: AsyncSession) -> None:
    await repo.insert_samples(
        db,
        [
            repo.NodeSampleInput(
                node="sfu-1", source="livekit-server", at_ms=0, outcome="timeout"
            )
        ],
    )
    row = (await repo.list_samples(db, node="sfu-1", source="livekit-server"))[0]
    assert row.outcome == "timeout"
    assert row.series_found is None  # no body was read, so nothing is known
    assert row.rooms is None


async def test_an_unknown_value_column_raises(db: AsyncSession) -> None:
    """A metric map naming a column the model lacks must not fail silently."""
    with pytest.raises(ValueError, match="unknown node_samples value column"):
        await repo.insert_samples(db, [_sample(0, packets_totl=1.0)])


async def test_a_value_too_large_for_int64_is_dropped_not_clamped(
    db: AsyncSession,
) -> None:
    """A clamped ceiling would become a real reading on a headroom chart."""
    await repo.insert_samples(db, [_sample(0, packet_bytes_total=1e30, rooms=2.0)])
    row = (await repo.list_samples(db, node="sfu-1", source="livekit-server"))[0]
    assert row.packet_bytes_total is None
    assert row.rooms == 2  # the rest of the sample still landed


async def test_byte_counters_survive_past_two_gibibytes(db: AsyncSession) -> None:
    """The INT4 trap: an SFU passes 2 GiB of RTP within hours."""
    big = 9_000_000_000.0
    await repo.insert_samples(db, [_sample(0, packet_bytes_total=big)])
    row = (await repo.list_samples(db, node="sfu-1", source="livekit-server"))[0]
    assert row.packet_bytes_total == 9_000_000_000


async def test_counter_and_gauge_sets_are_disjoint_and_cover_the_model() -> None:
    """Every value column is classified exactly once, or the diff whitelist lies."""
    assert not (repo.COUNTER_COLUMNS & repo.GAUGE_COLUMNS)
    model_columns = {
        column.name
        for column in NodeSample.__table__.columns  # type: ignore[attr-defined]
    }
    assert repo.VALUE_COLUMNS <= model_columns
    metadata = {
        "id",
        "node",
        "source",
        "at_ms",
        "project",
        "outcome",
        "series_found",
    }
    assert model_columns - metadata == repo.VALUE_COLUMNS


# ---------------------------------------------------------------------------
# Reads and the trim
# ---------------------------------------------------------------------------


async def test_list_samples_is_oldest_first_and_windowed(db: AsyncSession) -> None:
    await repo.insert_samples(
        db,
        [_sample(30_000, rooms=3.0), _sample(0, rooms=1.0), _sample(15_000, rooms=2.0)],
    )
    rows = await repo.list_samples(db, node="sfu-1", source="livekit-server")
    assert [r.at_ms for r in rows] == [0, 15_000, 30_000]
    windowed = await repo.list_samples(
        db, node="sfu-1", source="livekit-server", since_ms=10_000, until_ms=20_000
    )
    assert [r.at_ms for r in windowed] == [15_000]


async def test_reads_do_not_mix_nodes_or_sources(db: AsyncSession) -> None:
    """Diffing across two nodes or two exporters subtracts unrelated counters."""
    await repo.insert_samples(
        db,
        [
            _sample(0, packets_total=10.0),
            repo.NodeSampleInput(
                node="sfu-2",
                source="livekit-server",
                at_ms=0,
                outcome="ok",
                values={"packets_total": 9_999.0},
            ),
            repo.NodeSampleInput(
                node="sfu-1",
                source="node-exporter",
                at_ms=0,
                outcome="ok",
                values={"cpu_seconds_total": 5.0},
            ),
        ],
    )
    rows = await repo.list_samples(db, node="sfu-1", source="livekit-server")
    assert [r.packets_total for r in rows] == [10]


async def test_trim_deletes_only_rows_older_than_the_cutoff(db: AsyncSession) -> None:
    await repo.insert_samples(
        db, [_sample(1_000, rooms=1.0), _sample(5_000, rooms=2.0)]
    )
    removed = await repo.trim_older_than(db, cutoff_ms=3_000)
    assert removed == 1
    rows = await repo.list_samples(db, node="sfu-1", source="livekit-server")
    assert [r.at_ms for r in rows] == [5_000]


async def test_trim_is_bounded_to_one_batch_per_call(db: AsyncSession) -> None:
    """A backlog drains over ticks; no single call holds the write lock."""
    await repo.insert_samples(db, [_sample(i, rooms=1.0) for i in range(25)])
    assert await repo.trim_older_than(db, cutoff_ms=1_000, limit=10) == 10
    assert await repo.trim_older_than(db, cutoff_ms=1_000, limit=10) == 10
    assert await repo.trim_older_than(db, cutoff_ms=1_000, limit=10) == 5
    assert await repo.trim_older_than(db, cutoff_ms=1_000, limit=10) == 0
    assert await repo.count_samples(db) == 0


async def test_insert_samples_with_nothing_to_write_is_a_no_op(
    db: AsyncSession,
) -> None:
    assert await repo.insert_samples(db, []) == 0
    result = await db.execute(text("SELECT COUNT(*) FROM node_samples"))
    assert int(result.scalar() or 0) == 0


# ---------------------------------------------------------------------------
# utilisation_points: busy fraction from a total rate and its idle subset
#
# cpu_seconds_total is summed over every cpu AND every mode; cpu_idle_seconds_total
# is that same metric restricted to mode=idle. So the total rate is the core count
# and 1 - idle/total is utilisation exactly, with no core count needed.
# ---------------------------------------------------------------------------


def _rate(at_ms: int, per_second: float | None) -> repo.CounterRate:
    return repo.CounterRate(at_ms=at_ms, per_second=per_second)


def test_utilisation_is_one_minus_the_idle_share():
    # 8 cores: total rate 8.0 cpu-seconds per second. 2.0 idle means 75% busy.
    points = repo.utilisation_points([_rate(1000, 8.0)], [_rate(1000, 2.0)])
    assert [p.at_ms for p in points] == [1000]
    assert points[0].fraction == pytest.approx(0.75)


def test_a_fully_idle_machine_reads_zero_and_a_saturated_one_reads_one():
    idle = repo.utilisation_points([_rate(1, 4.0)], [_rate(1, 4.0)])
    assert idle[0].fraction == 0.0
    busy = repo.utilisation_points([_rate(1, 4.0)], [_rate(1, 0.0)])
    assert busy[0].fraction == 1.0


def test_the_core_count_never_has_to_be_supplied():
    """The same busy share on a 2-core and a 64-core box reads the same."""
    small = repo.utilisation_points([_rate(1, 2.0)], [_rate(1, 1.0)])
    large = repo.utilisation_points([_rate(1, 64.0)], [_rate(1, 32.0)])
    assert small[0].fraction == large[0].fraction == pytest.approx(0.5)


def test_an_unknown_rate_on_either_side_is_none_not_zero():
    """A reset arrives here as None from counter_rates and must stay None.

    0.0 would render as an idle node at exactly the moment something restarted.
    """
    assert (
        repo.utilisation_points([_rate(1, None)], [_rate(1, 2.0)])[0].fraction is None
    )
    assert (
        repo.utilisation_points([_rate(1, 8.0)], [_rate(1, None)])[0].fraction is None
    )
    assert (
        repo.utilisation_points([_rate(1, None)], [_rate(1, None)])[0].fraction is None
    )


def test_a_non_positive_capacity_rate_is_none():
    """Nothing to be a fraction of, so there is no fraction."""
    assert repo.utilisation_points([_rate(1, 0.0)], [_rate(1, 0.0)])[0].fraction is None
    assert (
        repo.utilisation_points([_rate(1, -1.0)], [_rate(1, 0.0)])[0].fraction is None
    )


def test_idle_above_capacity_is_none_rather_than_a_negative_utilisation():
    """The pair does not describe one machine, so it is not a measurement."""
    assert repo.utilisation_points([_rate(1, 4.0)], [_rate(1, 6.0)])[0].fraction is None


def test_float_noise_around_a_fully_idle_machine_clamps_to_zero():
    """idle marginally over total is division noise, not a negative reading."""
    points = repo.utilisation_points([_rate(1, 4.0)], [_rate(1, 4.0 * (1 + 1e-12))])
    assert points[0].fraction == 0.0


def test_the_series_are_paired_by_timestamp_never_by_position():
    """The two columns are read separately, so lengths can differ.

    Zipping positionally would compare a rate at one instant against a rate at
    another and produce a confident wrong number.
    """
    capacity = [_rate(1000, 8.0), _rate(2000, 8.0), _rate(3000, 8.0)]
    # The idle series is missing 2000 entirely and carries an extra 9000.
    idle = [_rate(1000, 2.0), _rate(3000, 4.0), _rate(9000, 0.0)]
    points = repo.utilisation_points(capacity, idle)
    assert [p.at_ms for p in points] == [1000, 2000, 3000]
    assert points[0].fraction == pytest.approx(0.75)
    # Present in capacity only: not a measurement.
    assert points[1].fraction is None
    assert points[2].fraction == pytest.approx(0.5)
    # An instant only the idle series saw is not invented into the output.
    assert 9000 not in [p.at_ms for p in points]


def test_it_consumes_counter_rates_output_end_to_end():
    """The reset rule is implemented once, in counter_rates, and survives here."""
    # A reboot zeroes both counters between the second and third sample.
    capacity = repo.counter_rates([(0, 800.0), (1000, 808.0), (2000, 0.0)])
    idle = repo.counter_rates([(0, 600.0), (1000, 602.0), (2000, 0.0)])
    points = repo.utilisation_points(capacity, idle)
    # First point of a series has no predecessor.
    assert points[0].fraction is None
    # 8 cpu-seconds in a second, 2 of them idle.
    assert points[1].fraction == pytest.approx(0.75)
    # The reset must not render as an idle machine.
    assert points[2].fraction is None


def test_empty_input_is_empty_output():
    assert repo.utilisation_points([], []) == []
    assert repo.utilisation_points([], [_rate(1, 1.0)]) == []
