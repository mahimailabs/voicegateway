"""node_exporter's own descriptor pair is scraped, so the gate has data.

A real seven-step run produced 44 gates, and seven of its nine UNKNOWNs were one
row repeated once per step::

    sura-sip-01/node-exporter/file_descriptors
    "was not measured: no row in the window carried both process_open_fds and
     process_max_fds"

**The resolution is to scrape it, not to suppress it.** There were three
positions available and only two of them are coherent:

* gate it and scrape it
* gate neither
* gate it but refuse to scrape it, which is what produced those seven rows

Suppressing was tried and rejected on the record: an earlier attempt keyed the
decision off :data:`SERIES` omitting the pair, and
``test_gate_only_where_measurable.py::test_node_exporter_keeps_its_file_descriptor_gate``
exists specifically to catch that. Its reasoning stands: node_exporter CAN
produce the pair (a live one reads ``process_open_fds`` 8 against
``process_max_fds`` 524287), so an absent reading was a gap rather than a
category error, and dropping the gate would hide a producible metric. Wiring the
series honours that decision rather than reversing it.

**What this does NOT claim.** These are node_exporter's own file handles. The
gate reads ``<node>/node-exporter/file_descriptors`` and means precisely that.
It is not evidence about livekit-sip's headroom, and the tests below pin the
services' own rows as the ones that answer the acceptance criterion.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from voicegateway.livekit_diag import gates
from voicegateway.loadtest import aggregation, judge
from voicegateway.middleware.node_samples_worker_middleware import (
    SERIES,
    SOURCE_LIVEKIT_SERVER,
    SOURCE_LIVEKIT_SIP,
    SOURCE_NODE_EXPORTER,
    columns_for,
)
from voicegateway.models.node_sample_model import NodeSample  # noqa: F401
from voicegateway.repository.node_samples_repository import (
    NodeSampleInput,
    insert_samples,
)

T0 = 1_785_520_800_000  # 2026-07-31T18:00:00Z
SEC = 1000
HEALTHY = {"name": "ramp-500", "attempted_calls": 15000, "succeeded_calls": 14985}

# Read off a live node_exporter, which is the only reason these are wired.
LIVE_OPEN_FDS = 8
LIVE_MAX_FDS = 524287


@pytest.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'procfds.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(
            SQLModel.metadata.create_all,
            tables=[SQLModel.metadata.tables["node_samples"]],
        )
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


def _sample(source: str, *, node: str = "sip-1", offset_s: int = 0, **values):
    return NodeSampleInput(
        node=node,
        source=source,
        at_ms=T0 + offset_s * SEC,
        outcome="ok",
        values=values,
    )


async def _fd_gates(db: AsyncSession):
    agg = await aggregation.aggregate_test_window(
        db, started_at_ms=T0, ended_at_ms=T0 + 60 * SEC
    )
    assert agg is not None
    return [
        g
        for g in judge.judge_test(HEALTHY, aggregate=agg)
        if g.subject and g.subject.endswith("/file_descriptors")
    ]


# --------------------------------------------------------------------------
# The map
# --------------------------------------------------------------------------


def test_node_exporter_is_wired_for_its_own_descriptor_pair() -> None:
    columns = columns_for(SOURCE_NODE_EXPORTER)
    assert "process_open_fds" in columns
    assert "process_max_fds" in columns


def test_the_metric_names_are_the_unprefixed_process_collector_ones() -> None:
    """node_exporter prefixes its COLLECTOR series with node_, not these.

    process_open_fds comes from the Go process collector every Prometheus client
    registers by default, so it carries no node_ prefix. Wiring it as
    node_process_open_fds would store NULL for the life of the deployment while
    nothing at runtime said so, which is the exact failure this map's provenance
    note exists to prevent.
    """
    metrics = {e.metric for e in SERIES[SOURCE_NODE_EXPORTER]}
    assert "process_open_fds" in metrics
    assert "node_process_open_fds" not in metrics


def test_the_same_pair_is_wired_for_the_services_under_test() -> None:
    """Non-vacuous: the services keep theirs, and theirs are the evidence."""
    for source in (SOURCE_LIVEKIT_SIP, SOURCE_LIVEKIT_SERVER):
        assert "process_open_fds" in columns_for(source)
        assert "process_max_fds" in columns_for(source)


def test_the_host_pair_is_still_node_exporter_alone() -> None:
    """The node-wide criterion is a different resource and has not moved."""
    assert "filefd_allocated" in columns_for(SOURCE_NODE_EXPORTER)
    for source in (SOURCE_LIVEKIT_SIP, SOURCE_LIVEKIT_SERVER):
        assert "filefd_allocated" not in columns_for(source)


# --------------------------------------------------------------------------
# What the gate does with the data
# --------------------------------------------------------------------------


async def test_a_scraped_node_exporter_now_grades_instead_of_unknown(
    db: AsyncSession,
) -> None:
    """The seven rows, resolved. Same numbers a live exporter reports."""
    await insert_samples(
        db,
        [
            _sample(
                SOURCE_NODE_EXPORTER,
                process_open_fds=LIVE_OPEN_FDS,
                process_max_fds=LIVE_MAX_FDS,
            )
        ],
    )
    [gate] = await _fd_gates(db)
    assert gate.subject == "sip-1/node-exporter/file_descriptors"
    assert gate.status == gates.PASS


async def test_the_row_names_the_exporter_so_it_cannot_be_read_as_the_service(
    db: AsyncSession,
) -> None:
    """The concern this change does not remove, pinned so it stays visible.

    A PASS here says node_exporter's own process has descriptor headroom. That
    is a true and narrow statement, and the only thing keeping it from reading
    as a claim about the SIP service is that the subject and the detail both
    name the exporter. If either ever stops doing so, this fails.
    """
    await insert_samples(
        db,
        [
            _sample(
                SOURCE_NODE_EXPORTER,
                process_open_fds=LIVE_OPEN_FDS,
                process_max_fds=LIVE_MAX_FDS,
            ),
            _sample(
                SOURCE_LIVEKIT_SIP, process_open_fds=120, process_max_fds=LIVE_MAX_FDS
            ),
        ],
    )
    fd = await _fd_gates(db)
    assert sorted(g.subject or "" for g in fd) == [
        "sip-1/livekit-sip/file_descriptors",
        "sip-1/node-exporter/file_descriptors",
    ]
    for gate in fd:
        source = (gate.subject or "").split("/")[1]
        assert source in gate.detail, gate


async def test_an_unscraped_node_exporter_is_still_unknown(db: AsyncSession) -> None:
    """The prior decision, untouched.

    Wiring the series does not mean every run carries them. An exporter that
    could have reported the pair and did not is still UNKNOWN, which is what
    ``test_node_exporter_keeps_its_file_descriptor_gate`` protects.
    """
    await insert_samples(db, [_sample(SOURCE_NODE_EXPORTER, filefd_allocated=2496.0)])
    [gate] = await _fd_gates(db)
    assert gate.status == gates.UNKNOWN


async def test_a_breaching_service_still_fails_next_to_a_passing_exporter(
    db: AsyncSession,
) -> None:
    """The rows must not drown each other out.

    node_exporter passing at 8 of 524287 says nothing about the service, and a
    service at its ceiling must still FAIL in the same run.
    """
    await insert_samples(
        db,
        [
            _sample(
                SOURCE_NODE_EXPORTER,
                process_open_fds=LIVE_OPEN_FDS,
                process_max_fds=LIVE_MAX_FDS,
            ),
            _sample(
                SOURCE_LIVEKIT_SIP,
                process_open_fds=520000,
                process_max_fds=LIVE_MAX_FDS,
            ),
        ],
    )
    fd = await _fd_gates(db)
    [failed] = [g for g in fd if g.status == gates.FAIL]
    assert "livekit-sip" in (failed.subject or "")
    [passed] = [g for g in fd if g.status == gates.PASS]
    assert "node-exporter" in (passed.subject or "")
