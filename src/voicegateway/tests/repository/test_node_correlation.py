"""Correlating a time window to node samples, WITHOUT attributing a node to a call.

Layer 7 has no per-call identity: ``livekit_packet_*``, ``nack_total`` and the
``livekit-sip`` counters are node-wide, so ``node_samples`` has no ``call_id``
and this correlation is an OVERLAP -- "these nodes were being scraped while that
call was open". Two concurrent calls therefore correlate to the same node, which
is asserted here, because it is the property that makes the difference between
correlation and attribution visible in the data rather than only in a docstring.

The other three things under test are the ways this surface could lie:

* a window nobody scraped coming back as a node with zeroed summaries (it comes
  back as ``no_samples``), and a window of failed scrapes coming back as a
  healthy one (it comes back as ``scrape_failed``);
* a counter reset inside the window turning into a rate -- the reset rule lives
  in ``node_samples_repository.counter_rates`` and is reused here, never
  reimplemented, which is asserted against that function's own output;
* a p95 computed from three samples.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from voicegateway.models import (  # noqa: F401 - registers calls + node_samples
    Call,
    NodeSample,
)
from voicegateway.repository import calls_repository as calls
from voicegateway.repository import node_correlation_repository as correlate
from voicegateway.repository import node_samples_repository as samples

# A realistic epoch millisecond, so padding never has to reach below zero.
_T0 = 1_700_000_000_000
_CALL_END = _T0 + 60_000


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


def _sample(
    at_ms: int,
    *,
    node: str = "sfu-1",
    source: str = "livekit-server",
    outcome: str = "ok",
    **values: float | None,
) -> samples.NodeSampleInput:
    return samples.NodeSampleInput(
        node=node,
        source=source,
        at_ms=at_ms,
        outcome=outcome,
        series_found=len(values) or None,
        values=values,
    )


async def _call_row(
    db: AsyncSession,
    *,
    attempt_id: str = "attempt-1",
    started_at_ms: int | None = _T0,
    ended_at_ms: int | None = _CALL_END,
) -> calls.CallRow:
    """One real ``calls`` row, written through the repository that owns it."""
    call_id = await calls.upsert_call(
        db,
        origin="loadgen",
        attempt_id=attempt_id,
        started_at_ms=started_at_ms,
        ended_at_ms=ended_at_ms,
    )
    row = await calls.get_call(db, call_id)
    assert row is not None
    return row


# ---------------------------------------------------------------------------
# The window: padded, and honest about it
# ---------------------------------------------------------------------------


async def test_window_for_call_reports_both_the_requested_and_padded_bounds(
    db: AsyncSession,
) -> None:
    """"Within 15 s of this call" is a weaker claim than "during this call".

    Both bounds are carried so a renderer can tell which one it is holding.
    """
    window = correlate.window_for_call(await _call_row(db))
    assert window is not None
    assert (window.requested_start_ms, window.requested_end_ms) == (_T0, _CALL_END)
    assert window.pad_ms == correlate.DEFAULT_WINDOW_PAD_MS
    assert window.start_ms == _T0 - correlate.DEFAULT_WINDOW_PAD_MS
    assert window.end_ms == _CALL_END + correlate.DEFAULT_WINDOW_PAD_MS


async def test_padding_is_a_parameter_not_a_magic_number(db: AsyncSession) -> None:
    window = correlate.window_for_call(await _call_row(db), pad_ms=0)
    assert window is not None
    assert (window.start_ms, window.end_ms) == (_T0, _CALL_END)
    assert window.pad_ms == 0


async def test_a_call_still_in_flight_has_no_window(db: AsyncSession) -> None:
    """Substituting "now" for a missing end would make the overlap set depend on
    when the page was loaded."""
    call = await _call_row(db, ended_at_ms=None)
    assert call.ended_at_ms is None
    assert correlate.window_for_call(call) is None


async def test_a_call_that_never_started_has_no_window(db: AsyncSession) -> None:
    """An INVITE that never produced a room: the row that matters most in a load
    test, and the one with no span to correlate over."""
    call = await _call_row(db, started_at_ms=None, ended_at_ms=None)
    assert correlate.window_for_call(call) is None


async def test_an_inverted_span_is_refused(db: AsyncSession) -> None:
    with pytest.raises(ValueError, match="precedes start"):
        correlate.window_of(_CALL_END, _T0)


async def test_a_negative_pad_is_refused() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        correlate.window_of(_T0, _CALL_END, pad_ms=-1)


# ---------------------------------------------------------------------------
# An empty window is not a healthy node
# ---------------------------------------------------------------------------


async def test_a_window_nobody_scraped_is_no_samples_never_zeros(
    db: AsyncSession,
) -> None:
    """No scrape worker, no configured target, or a window past the trim.

    The one thing this must never do is name a node with zeroed summaries: a flat
    zero line reads as a clean bill of health.
    """
    result = await correlate.correlate_window(
        db, window=correlate.window_of(_T0, _CALL_END)
    )
    assert result.status == "no_samples"
    assert result.nodes_sampled == []


async def test_a_window_of_failed_scrapes_is_not_a_healthy_node(
    db: AsyncSession,
) -> None:
    """T3 writes a row with an outcome and NULL values for every failed scrape.

    That distinction has to survive into the correlation: the node was watched,
    the watching did not work, and no number may be published from it.
    """
    await samples.insert_samples(
        db,
        [
            _sample(_T0 + 1_000, outcome="timeout"),
            _sample(_T0 + 16_000, outcome="unreachable"),
        ],
    )
    result = await correlate.correlate_window(
        db, window=correlate.window_of(_T0, _CALL_END)
    )

    assert result.status == "scrape_failed"
    entry = result.nodes_sampled[0]
    assert entry.samples == 2
    assert entry.ok_samples == 0
    assert entry.failed_samples == 2
    assert entry.outcomes == {"timeout": 1, "unreachable": 1}
    # Nothing was measured, so nothing is reported as measured.
    assert entry.gauges["rooms"].samples == 0
    assert entry.gauges["rooms"].maximum is None
    assert entry.gauges["rooms"].peak is None
    assert entry.gauges["rooms"].peak_stat == "not_measured"
    packets = entry.counters["packets_total"]
    assert packets.peak_per_second is None
    assert packets.unknown_points == packets.points


async def test_every_status_is_in_the_closed_set(db: AsyncSession) -> None:
    await samples.insert_samples(db, [_sample(_T0 + 1_000, rooms=3.0)])
    window = correlate.window_of(_T0, _CALL_END)
    result = await correlate.correlate_window(db, window=window)
    assert result.status == "correlated"
    assert result.status in correlate.WINDOW_STATUSES


# ---------------------------------------------------------------------------
# Correlation, not attribution
# ---------------------------------------------------------------------------


async def test_two_overlapping_calls_correlate_to_the_same_node(
    db: AsyncSession,
) -> None:
    """The property that separates an overlap from a join.

    If this were attribution, one of these calls would have to lose. Both get the
    same samples, because the samples describe the box, not either call.
    """
    await samples.insert_samples(db, [_sample(_T0 + 20_000, rooms=2.0)])
    first = await _call_row(db, attempt_id="a")
    second = await _call_row(
        db, attempt_id="b", started_at_ms=_T0 + 10_000, ended_at_ms=_CALL_END + 10_000
    )

    one = await correlate.correlate_call_window(db, call=first)
    two = await correlate.correlate_call_window(db, call=second)
    assert one is not None and two is not None
    assert [e.node for e in one.nodes_sampled] == ["sfu-1"]
    assert [e.node for e in two.nodes_sampled] == ["sfu-1"]
    assert one.nodes_sampled[0].gauges["rooms"].maximum == 2.0
    assert two.nodes_sampled[0].gauges["rooms"].maximum == 2.0


async def test_correlate_call_window_returns_none_only_for_a_call_with_no_window(
    db: AsyncSession,
) -> None:
    """None means the CALL has no span; a scraped-nothing window is a result."""
    in_flight = await _call_row(db, attempt_id="open", ended_at_ms=None)
    assert await correlate.correlate_call_window(db, call=in_flight) is None

    closed = await correlate.correlate_call_window(db, call=await _call_row(db))
    assert closed is not None
    assert closed.status == "no_samples"


def test_the_correlation_surface_carries_no_pointer_to_a_call() -> None:
    """Layer 7 correlates by (node, time window), never by FK.

    Nothing returned here may name a call, and the table it reads may not gain a
    foreign key: a per-call node counter would be a measurement that does not
    exist server-side.
    """
    for dataclass_type in (
        correlate.NodeWindow,
        correlate.SampledTarget,
        correlate.GaugeSummary,
        correlate.CounterRateSummary,
        correlate.NodeSamplesInWindow,
        correlate.WindowCorrelation,
    ):
        assert is_dataclass(dataclass_type)
        for field in fields(dataclass_type):
            assert "call" not in field.name, (
                f"{dataclass_type.__name__}.{field.name} names a call; an overlap "
                "must not be presented as an attribution"
            )
    table = NodeSample.__table__  # type: ignore[attr-defined]
    assert not table.foreign_keys
    assert "call_id" not in table.columns
    assert "node_id" not in table.columns


# ---------------------------------------------------------------------------
# Rates: the ONE reset implementation, reused
# ---------------------------------------------------------------------------


async def test_a_counter_reset_inside_the_window_is_unknown_not_a_spike(
    db: AsyncSession,
) -> None:
    """A livekit-server restart zeroes every _total mid-window."""
    await samples.insert_samples(
        db,
        [
            _sample(_T0, packets_total=1_000.0),
            _sample(_T0 + 15_000, packets_total=4_000.0),
            _sample(_T0 + 30_000, packets_total=12.0),  # restart
            _sample(_T0 + 45_000, packets_total=3_012.0),
        ],
    )
    result = await correlate.correlate_window(
        db, window=correlate.window_of(_T0, _CALL_END, pad_ms=0)
    )
    packets = result.nodes_sampled[0].counters["packets_total"]

    assert packets.points == 4
    # The first point (no predecessor inside or before the window) and the reset.
    assert packets.unknown_points == 2
    assert packets.peak_per_second == pytest.approx(200.0)
    assert packets.peak_per_second > 0, "a reset must not surface as a huge spike"


async def test_the_reset_rule_is_not_reimplemented_here(db: AsyncSession) -> None:
    """The summary is exactly what counter_rates says, point for point.

    Two implementations of a reset rule is how one of them silently disagrees.
    """
    points = [
        (_T0, 1_000.0),
        (_T0 + 15_000, 4_000.0),
        (_T0 + 30_000, 12.0),
        (_T0 + 45_000, None),
        (_T0 + 60_000, 900.0),
    ]
    await samples.insert_samples(
        db, [_sample(at_ms, packets_total=value) for at_ms, value in points]
    )
    expected = samples.counter_rates(points)
    expected_known = [r.per_second for r in expected if r.per_second is not None]

    result = await correlate.correlate_window(
        db, window=correlate.window_of(_T0, _CALL_END, pad_ms=0)
    )
    packets = result.nodes_sampled[0].counters["packets_total"]
    assert packets.points == len(expected)
    assert packets.unknown_points == len(expected) - len(expected_known)
    assert packets.peak_per_second == pytest.approx(max(expected_known))


async def test_the_lead_in_gives_the_first_in_window_sample_a_rate(
    db: AsyncSession,
) -> None:
    """Without a predecessor the window's opening point has no rate at all."""
    await samples.insert_samples(
        db,
        [
            _sample(_T0 - 15_000, packets_total=1_000.0),  # before the window
            _sample(_T0 + 1_000, packets_total=4_200.0),
        ],
    )
    window = correlate.window_of(_T0, _CALL_END, pad_ms=0)

    with_lead_in = await correlate.correlate_window(db, window=window)
    packets = with_lead_in.nodes_sampled[0].counters["packets_total"]
    assert packets.points == 1, "the lead-in sample is a diff input, not a result"
    assert packets.unknown_points == 0
    assert packets.peak_per_second == pytest.approx(200.0)

    without = await correlate.correlate_window(db, window=window, lead_in_ms=0)
    assert without.nodes_sampled[0].counters["packets_total"].unknown_points == 1


async def test_a_lead_in_older_than_the_bound_is_not_used(db: AsyncSession) -> None:
    """Diffing across an hour-long gap would spread that hour over one instant."""
    await samples.insert_samples(
        db,
        [
            _sample(_T0 - 3_600_000, packets_total=1_000.0),
            _sample(_T0 + 1_000, packets_total=4_200.0),
        ],
    )
    result = await correlate.correlate_window(
        db, window=correlate.window_of(_T0, _CALL_END, pad_ms=0)
    )
    packets = result.nodes_sampled[0].counters["packets_total"]
    assert packets.points == 1
    assert packets.unknown_points == 1
    assert packets.peak_per_second is None


async def test_lead_in_samples_are_never_counted_or_summarized(
    db: AsyncSession,
) -> None:
    await samples.insert_samples(
        db,
        [
            _sample(_T0 - 15_000, rooms=99.0),  # before the window
            _sample(_T0 + 1_000, rooms=2.0),
        ],
    )
    result = await correlate.correlate_window(
        db, window=correlate.window_of(_T0, _CALL_END, pad_ms=0)
    )
    entry = result.nodes_sampled[0]
    assert entry.samples == 1
    assert entry.first_sample_at_ms == _T0 + 1_000
    assert entry.gauges["rooms"].maximum == 2.0


async def test_a_negative_lead_in_is_refused(db: AsyncSession) -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        await correlate.correlate_window(
            db, window=correlate.window_of(_T0, _CALL_END), lead_in_ms=-1
        )


# ---------------------------------------------------------------------------
# Percentiles need samples
# ---------------------------------------------------------------------------


async def test_a_peak_from_fewer_than_ten_samples_is_a_max_of_n(
    db: AsyncSession,
) -> None:
    await samples.insert_samples(
        db,
        [
            _sample(_T0 + i * 15_000, load1=float(i), source="node-exporter")
            for i in range(4)
        ],
    )
    result = await correlate.correlate_window(
        db, window=correlate.window_of(_T0, _T0 + 300_000)
    )
    load1 = result.nodes_sampled[0].gauges["load1"]
    assert load1.samples == 4
    assert load1.peak_stat == "max_of_n"
    assert load1.peak == 3.0


async def test_a_peak_from_ten_samples_is_a_p95(db: AsyncSession) -> None:
    await samples.insert_samples(
        db,
        [
            _sample(_T0 + i * 15_000, load1=float(i + 1), source="node-exporter")
            for i in range(correlate.MIN_PERCENTILE_SAMPLES)
        ],
    )
    result = await correlate.correlate_window(
        db, window=correlate.window_of(_T0, _T0 + 300_000)
    )
    load1 = result.nodes_sampled[0].gauges["load1"]
    assert load1.samples == correlate.MIN_PERCENTILE_SAMPLES
    assert load1.peak_stat == "p95"
    assert load1.peak is not None
    assert load1.maximum is not None
    assert load1.peak < load1.maximum
    assert load1.peak_stat in correlate.PEAK_STATS


# ---------------------------------------------------------------------------
# Window bounds, filters, and column validation
# ---------------------------------------------------------------------------


async def test_samples_outside_the_padded_window_do_not_appear(
    db: AsyncSession,
) -> None:
    await samples.insert_samples(
        db,
        [
            _sample(_T0 - 20_000, rooms=7.0),  # before start - pad
            _sample(_T0 + 5_000, rooms=2.0),
            _sample(_CALL_END + 20_000, rooms=9.0),  # after end + pad
        ],
    )
    result = await correlate.correlate_window(
        db, window=correlate.window_of(_T0, _CALL_END)
    )
    entry = result.nodes_sampled[0]
    assert entry.samples == 1
    assert entry.gauges["rooms"].maximum == 2.0


async def test_gauges_are_reported_as_stored_never_diffed(db: AsyncSession) -> None:
    """The rate of change of filefd_allocated is not what anyone means by it."""
    await samples.insert_samples(
        db,
        [
            _sample(_T0 + 1_000, source="node-exporter", filefd_allocated=1_000.0),
            _sample(_T0 + 16_000, source="node-exporter", filefd_allocated=1_200.0),
        ],
    )
    result = await correlate.correlate_window(
        db, window=correlate.window_of(_T0, _CALL_END)
    )
    fd = result.nodes_sampled[0].gauges["filefd_allocated"]
    assert (fd.minimum, fd.maximum, fd.latest) == (1_000.0, 1_200.0, 1_200.0)
    assert "filefd_allocated" not in result.nodes_sampled[0].counters


async def test_node_and_source_filters_narrow_the_correlation(
    db: AsyncSession,
) -> None:
    await samples.insert_samples(
        db,
        [
            _sample(_T0 + 1_000, rooms=1.0),
            _sample(_T0 + 1_000, node="sfu-2", rooms=2.0),
            _sample(_T0 + 1_000, source="node-exporter", load1=0.5),
        ],
    )
    window = correlate.window_of(_T0, _CALL_END)

    everything = await correlate.correlate_window(db, window=window)
    assert {(e.node, e.source) for e in everything.nodes_sampled} == {
        ("sfu-1", "livekit-server"),
        ("sfu-1", "node-exporter"),
        ("sfu-2", "livekit-server"),
    }

    one_node = await correlate.correlate_window(db, window=window, node="sfu-2")
    assert [(e.node, e.source) for e in one_node.nodes_sampled] == [
        ("sfu-2", "livekit-server")
    ]

    one_source = await correlate.correlate_window(
        db, window=window, source="node-exporter"
    )
    assert [(e.node, e.source) for e in one_source.nodes_sampled] == [
        ("sfu-1", "node-exporter")
    ]


async def test_list_nodes_sampled_in_window_counts_the_targets(
    db: AsyncSession,
) -> None:
    await samples.insert_samples(
        db,
        [
            _sample(_T0 + 1_000, rooms=1.0),
            _sample(_T0 + 16_000, rooms=2.0),
            _sample(_T0 + 1_000, node="sfu-2", outcome="timeout"),
        ],
    )
    targets = await correlate.list_nodes_sampled_in_window(
        db, window=correlate.window_of(_T0, _CALL_END)
    )
    assert [(t.node, t.source, t.samples) for t in targets] == [
        ("sfu-1", "livekit-server", 2),
        ("sfu-2", "livekit-server", 1),
    ]


async def test_a_gauge_asked_for_as_a_counter_is_refused(db: AsyncSession) -> None:
    """Silently ignoring it would make the column read as "not measured"."""
    with pytest.raises(ValueError, match="not node_samples counter columns"):
        await correlate.correlate_window(
            db,
            window=correlate.window_of(_T0, _CALL_END),
            counters=["filefd_allocated"],
        )


async def test_an_unknown_column_is_refused(db: AsyncSession) -> None:
    with pytest.raises(ValueError, match="not node_samples gauge columns"):
        await correlate.correlate_window(
            db,
            window=correlate.window_of(_T0, _CALL_END),
            gauges=["1; DROP TABLE calls"],
        )


async def test_a_truncated_read_says_so(db: AsyncSession) -> None:
    """A prefix of the window must not read as the whole window."""
    await samples.insert_samples(
        db, [_sample(_T0 + i * 1_000, rooms=float(i)) for i in range(6)]
    )
    result = await correlate.correlate_window(
        db, window=correlate.window_of(_T0, _CALL_END), limit=3
    )
    entry = result.nodes_sampled[0]
    assert entry.truncated is True
    assert entry.samples == 3


# ---------------------------------------------------------------------------
# The M4 headline shape
# ---------------------------------------------------------------------------


async def test_the_ramp_window_surfaces_file_descriptor_saturation(
    db: AsyncSession,
) -> None:
    """"The knee at 25 clients CORRELATED WITH filefd_allocated hitting
    filefd_maximum on one host."

    One host walks into its fd ceiling during the ramp step; a second host is not
    scraped at all in that window and therefore does not appear -- it is not
    reported as healthy, because nobody looked at it.
    """
    ramp_start, ramp_end = _T0, _T0 + 120_000
    await samples.insert_samples(
        db,
        [
            _sample(
                ramp_start + i * 15_000,
                node="sfu-1",
                source="node-exporter",
                filefd_allocated=float(1_000 + i * 200),
                filefd_maximum=1_800.0,
            )
            for i in range(5)
        ],
    )
    # sfu-2 was only scraped long after the ramp.
    await samples.insert_samples(
        db,
        [
            _sample(
                ramp_end + 3_600_000,
                node="sfu-2",
                source="node-exporter",
                filefd_allocated=12.0,
                filefd_maximum=1_800.0,
            )
        ],
    )

    result = await correlate.correlate_window(
        db, window=correlate.window_of(ramp_start, ramp_end)
    )
    assert result.status == "correlated"
    assert [e.node for e in result.nodes_sampled] == ["sfu-1"]

    entry = result.nodes_sampled[0]
    allocated = entry.gauges["filefd_allocated"]
    ceiling = entry.gauges["filefd_maximum"]
    assert allocated.maximum == 1_800.0
    assert ceiling.maximum == 1_800.0
    assert allocated.maximum == ceiling.latest, "allocated reached the ceiling"
    assert allocated.peak_stat == "max_of_n"  # five samples is not a p95
