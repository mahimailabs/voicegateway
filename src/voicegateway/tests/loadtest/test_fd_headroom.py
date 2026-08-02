"""The file-descriptor headroom gate, from scrape to verdict.

The pair was scraped into ``node_samples`` and never reached the gate: the judge
hardcoded the reading as unmeasured, so ``resource_headroom/file_descriptors``
returned UNKNOWN no matter what node-exporter reported. A criterion that can
only ever be UNKNOWN is a criterion nobody can pass.

Four properties are pinned here.

**The per-process pair, not the host one.** On a real box the host maximum reads
9.223372036854776e18, which is effectively unbounded, does not fit a 64-bit
column, and yields no meaningful ratio; ``process_max_fds`` on the same box
reads 524287, which is the ceiling the service actually hits.

**Worst over the window, not last.** Headroom is a floor. A run that touched 95%
once has demonstrated it can get there, whatever it settled back to.

**Paired within one row.** An open count from one scrape against a limit from
another describes an instant that never existed, exactly as with memory.

**Both halves NULL stays UNKNOWN, with a reason.** This is the trap. The gate
must never read an unmeasured pair as headroom nobody breached.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from voicegateway.livekit_diag import gates
from voicegateway.loadtest import aggregation, judge
from voicegateway.models.node_sample_model import NodeSample  # noqa: F401
from voicegateway.repository.node_correlation_repository import window_of
from voicegateway.repository.node_samples_repository import (
    NodeSampleInput,
    insert_samples,
)

T0 = 1_785_520_800_000  # 2026-07-31T18:00:00Z
SEC = 1000
HEALTHY = {"name": "ramp-500", "attempted_calls": 15000, "succeeded_calls": 14985}


@pytest.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fds.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(
            SQLModel.metadata.create_all,
            tables=[SQLModel.metadata.tables["node_samples"]],
        )
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


def _sample(offset_s: int, *, node: str = "sip-1", **values) -> NodeSampleInput:
    return NodeSampleInput(
        node=node,
        source="livekit-sip",
        at_ms=T0 + offset_s * SEC,
        outcome="ok",
        values=values,
    )


async def _aggregate(db: AsyncSession, last_offset_s: int):
    return await aggregation.aggregate_test_window(
        db, started_at_ms=T0, ended_at_ms=T0 + last_offset_s * SEC
    )


def _fd(agg) -> gates.HeadroomReading:
    [reading] = agg.fd_readings
    return reading


# --------------------------------------------------------------------------
# The pair reaches the aggregate
# --------------------------------------------------------------------------


async def test_the_pair_is_carried_onto_the_aggregate(db: AsyncSession) -> None:
    await insert_samples(
        db,
        [
            _sample(0, process_open_fds=11, process_max_fds=524287),
            _sample(10, process_open_fds=300, process_max_fds=524287),
        ],
    )
    agg = await _aggregate(db, 10)
    assert agg is not None
    reading = _fd(agg)
    assert (reading.used, reading.limit) == (300.0, 524287.0)
    assert reading.unmeasured_reason is None


async def test_the_worst_reading_wins_not_the_last(db: AsyncSession) -> None:
    """Headroom is a floor. Settling back does not undo having been there."""
    await insert_samples(
        db,
        [
            _sample(0, process_open_fds=100, process_max_fds=1000),
            _sample(10, process_open_fds=950, process_max_fds=1000),  # 5% left
            _sample(20, process_open_fds=120, process_max_fds=1000),
        ],
    )
    agg = await _aggregate(db, 20)
    assert agg is not None
    assert _fd(agg).used == 950.0
    # And the gate fails on it, rather than passing on the quiet last sample.
    [gate] = gates.headroom_gates([_fd(agg)])
    assert gate.status == gates.FAIL


async def test_the_pair_is_read_from_one_row(db: AsyncSession) -> None:
    """max(open) against max(limit) across rows describes no real instant.

    Here the row with the most open descriptors also has the largest ceiling,
    so a cross-row pairing would compute 900/1000 = 10% headroom left, when
    every instant that occurred had at least 50%.
    """
    await insert_samples(
        db,
        [
            _sample(0, process_open_fds=500, process_max_fds=1000),
            _sample(10, process_open_fds=900, process_max_fds=2000),
        ],
    )
    agg = await _aggregate(db, 10)
    assert agg is not None
    reading = _fd(agg)
    # Both rows sit at exactly 50% used, so either pairing is the same ratio.
    assert reading.used / reading.limit == pytest.approx(0.5)
    [gate] = gates.headroom_gates([reading])
    assert gate.status == gates.PASS


# --------------------------------------------------------------------------
# The trap: unmeasured stays UNKNOWN
# --------------------------------------------------------------------------


async def test_a_window_with_no_pair_is_unmeasured_with_a_reason(
    db: AsyncSession,
) -> None:
    """The trap. Both halves NULL must never read as headroom to spare."""
    await insert_samples(db, [_sample(0, rooms=3), _sample(10, rooms=4)])
    agg = await _aggregate(db, 10)
    assert agg is not None
    reading = _fd(agg)
    assert (reading.used, reading.limit) == (None, None)
    assert reading.unmeasured_reason
    [gate] = gates.headroom_gates([reading])
    assert gate.status == gates.UNKNOWN
    assert gate.status != gates.PASS


async def test_a_row_missing_one_half_contributes_nothing(db: AsyncSession) -> None:
    """An open count with no ceiling is not a ratio, and neither is the reverse."""
    await insert_samples(
        db,
        [
            _sample(0, process_open_fds=900),
            _sample(10, process_max_fds=1000),
        ],
    )
    agg = await _aggregate(db, 10)
    assert agg is not None
    assert _fd(agg).used is None
    assert _fd(agg).unmeasured_reason


async def test_a_zero_limit_is_not_divided_by(db: AsyncSession) -> None:
    """A ceiling of zero is a broken scrape, not a node with no headroom."""
    await insert_samples(db, [_sample(0, process_open_fds=5, process_max_fds=0)])
    agg = await _aggregate(db, 0)
    assert agg is not None
    assert _fd(agg).used is None


# --------------------------------------------------------------------------
# The judge consumes what was measured
# --------------------------------------------------------------------------


async def test_the_gate_reports_the_measured_pair(db: AsyncSession) -> None:
    """End to end: the reason this node exists.

    Before this, the judge hardcoded the reading as unmeasured and this gate
    could not pass whatever the box reported.
    """
    await insert_samples(
        db,
        [
            _sample(0, cpu_seconds_total=1000.0, cpu_idle_seconds_total=800.0),
            _sample(
                10,
                cpu_seconds_total=1040.0,
                cpu_idle_seconds_total=830.0,
                process_open_fds=11,
                process_max_fds=524287,
            ),
        ],
    )
    agg = await _aggregate(db, 10)
    assert agg is not None
    results = judge.judge_test(HEALTHY, aggregate=agg)
    [fd_gate] = [
        r for r in results if r.subject and r.subject.endswith("/file_descriptors")
    ]
    assert fd_gate.status == gates.PASS


async def test_an_unscraped_pair_leaves_the_gate_unknown(db: AsyncSession) -> None:
    """Non-vacuous companion to the test above: it does not always pass."""
    await insert_samples(
        db,
        [
            _sample(0, cpu_seconds_total=1000.0, cpu_idle_seconds_total=800.0),
            _sample(10, cpu_seconds_total=1040.0, cpu_idle_seconds_total=830.0),
        ],
    )
    agg = await _aggregate(db, 10)
    assert agg is not None
    results = judge.judge_test(HEALTHY, aggregate=agg)
    [fd_gate] = [
        r for r in results if r.subject and r.subject.endswith("/file_descriptors")
    ]
    assert fd_gate.status == gates.UNKNOWN


def test_an_aggregate_carrying_no_fd_readings_still_produces_the_gate() -> None:
    """The silent-drop guard.

    A caller that builds an aggregate without FD readings must not lose the
    file-descriptor gate while keeping RTP ports and network. An absent gate
    reads as a resource nobody had to satisfy; UNKNOWN says nobody measured it.
    """
    agg = aggregation.TestAggregate(
        window=window_of(T0, T0 + 10 * SEC),
        peak_cpu_utilisation=0.5,
        peak_memory_utilisation=0.4,
        node_samples_in_window=2,
        cpu_readings=[
            gates.NodeUtilisationReading(node="sfu-1", utilisation=0.5, samples=2)
        ],
        memory_readings=[
            gates.NodeUtilisationReading(node="sfu-1", utilisation=0.4, samples=2)
        ],
    )
    # judge_run, because RTP ports and network are emitted once per run rather
    # than once per node per test. Every assertion below is unchanged.
    results = judge.judge_run([HEALTHY], aggregates={HEALTHY["name"]: agg})
    resources = {
        r.subject.split("/")[-1] for r in results if r.subject and "/" in r.subject
    }
    assert {"file_descriptors", "rtp_ports", "network"} <= resources
    [fd_gate] = [r for r in results if r.subject == "sfu-1/file_descriptors"]
    assert fd_gate.status == gates.UNKNOWN


# --------------------------------------------------------------------------
# The host pair is deliberately not the one judged
# --------------------------------------------------------------------------


def test_the_judged_columns_are_the_per_process_pair() -> None:
    """Named explicitly, because the host pair is the tempting wrong choice.

    ``node_filefd_maximum`` on an observed box reads 9.223372036854776e18. A
    ratio against that is zero for every workload that will ever run.
    """
    assert aggregation.FD_USED_COLUMN == "process_open_fds"
    assert aggregation.FD_LIMIT_COLUMN == "process_max_fds"
    assert "filefd" not in aggregation.FD_LIMIT_COLUMN


async def test_the_host_filefd_pair_alone_does_not_satisfy_the_gate(
    db: AsyncSession,
) -> None:
    """Behavioural, not textual. Scraping only the host pair judges nothing."""
    await insert_samples(
        db, [_sample(0, filefd_allocated=1216, filefd_maximum=9223372036854774784)]
    )
    agg = await _aggregate(db, 0)
    assert agg is not None
    assert _fd(agg).used is None
    [gate] = gates.headroom_gates([_fd(agg)])
    assert gate.status == gates.UNKNOWN


# --------------------------------------------------------------------------
# One node, several exporters: the gates must be tellable apart
# --------------------------------------------------------------------------


async def test_gates_for_one_node_across_sources_have_distinct_subjects(
    db: AsyncSession,
) -> None:
    """Found by a live run, invisible to every single-source fixture.

    A node is commonly scraped by three exporters, and only one of them carries
    the per-process descriptor pair. The subject dropped the source, so the run
    emitted three gates labelled identically reading PASS, UNKNOWN and UNKNOWN,
    with nothing saying which exporter passed. A reader cannot act on that, and
    the PASS is the half that looks like coverage.
    """
    await insert_samples(
        db,
        [
            _sample(0, process_open_fds=11, process_max_fds=524287),
            NodeSampleInput(
                node="sip-1",
                source="node-exporter",
                at_ms=T0,
                outcome="ok",
                values={"filefd_allocated": 2496.0},
            ),
        ],
    )
    agg = await _aggregate(db, 0)
    assert agg is not None
    fd_gates = [
        g
        for g in judge.judge_test(HEALTHY, aggregate=agg)
        if g.subject and g.subject.endswith("/file_descriptors")
    ]
    assert len(fd_gates) == 2
    assert len({g.subject for g in fd_gates}) == 2, [g.subject for g in fd_gates]
    # And the statuses really do differ, so the collision was not cosmetic.
    assert {g.status for g in fd_gates} == {gates.PASS, gates.UNKNOWN}
    # The one that passed says which exporter it came from.
    [passed] = [g for g in fd_gates if g.status == gates.PASS]
    assert "livekit-sip" in passed.subject
    assert "livekit-sip" in passed.detail


def test_a_reading_with_no_source_keeps_the_short_subject() -> None:
    """A caller holding only a node name is unaffected by the change."""
    [gate] = gates.headroom_gates(
        [
            gates.HeadroomReading(
                node="sfu-1", resource="file_descriptors", used=400, limit=1000
            )
        ]
    )
    assert gate.subject == "sfu-1/file_descriptors"
