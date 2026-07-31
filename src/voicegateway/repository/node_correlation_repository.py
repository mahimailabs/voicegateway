"""Correlate a TIME WINDOW to ``node_samples``. By overlap, never by foreign key.

**This module owns no table and writes nothing.** It is the read side of layer 7,
and it exists because layer 7 has no per-call identity to join on:
``livekit_packet_*``, ``livekit_nack_total`` and the ``livekit-sip``
``invite_*``/``calls_*`` families are NODE counters, and ``node_samples`` has no
``call_id`` column by design (see the model docstring). There is no ``node_id``
awareness anywhere in this tree, and this module does not add any.

**Overlap is not attribution, and the names here have to survive that.** A node
that appears in a :class:`WindowCorrelation` means exactly one thing: *rows exist
for that node with an ``at_ms`` inside this window*. It does NOT mean the node
served the call whose timestamps produced the window, that the call's media
crossed it, or that its packet loss belonged to that call. Two concurrent calls
correlate to the same nodes; a call that ran on a node nobody scrapes correlates
to none. Everything returned here is "what the boxes looked like while this
happened", which is what makes the M4 sentence honest: *the knee at 25 clients
**correlated with** ``filefd_allocated`` reaching ``filefd_maximum`` on one
host*, not *caused by*.

Four properties this module exists to hold:

* **A window with no samples is not a healthy node.** Nothing scraped in the
  window means :data:`WINDOW_STATUSES`\\ ``[2]`` (``"no_samples"``) and an empty
  list -- never a node with zeroed summaries. Samples that all failed to scrape
  mean ``"scrape_failed"``, which is again not a reading. T3 writes a row with an
  ``outcome`` and NULL values for every failed scrape precisely so these three
  cases stay distinguishable, and nothing here collapses them.

* **Every rate comes from** :func:`node_samples_repository.counter_rates`, the
  ONE counter-reset implementation. A reset inside the window makes that point's
  rate ``None``, and ``None`` points are counted (``unknown_points``) rather than
  dropped, replaced by 0, or re-derived. This module deliberately does NOT report
  *why* a point is unknown -- first point, NULL end, non-positive dt, or reset --
  because deciding that here would be a second implementation of the reset rule,
  and two implementations of one rule is how a wrong number gets published.

* **A percentile needs samples.** Below :data:`MIN_PERCENTILE_SAMPLES` the peak
  statistic is the maximum of N and says so (``peak_stat == "max_of_n"``), never
  a p95 interpolated from three points. With nothing measured it is
  ``"not_measured"`` and the value is ``None``.

* **Padding is named, not magic.** The scrape grid is coarse (15 s) next to a
  short call, and the scrape host's clock is not the clock that stamped the
  webhook. :data:`DEFAULT_WINDOW_PAD_MS` widens the window on both sides by one
  scrape interval, and the returned :class:`NodeWindow` carries both the padded
  and the requested bounds so a reader can always see how much was added and is
  never told the padded region is part of the call.

A source only exposes its own series (``node-exporter`` has no ``rooms``,
``livekit-server`` has no ``filefd_allocated``), so a summary of
``"not_measured"`` for the other source's columns is expected and correct: it
says this scrape did not measure it, which is true.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from voicegateway.repository.node_samples_repository import (
    COUNTER_COLUMNS,
    DEFAULT_READ_LIMIT,
    GAUGE_COLUMNS,
    counter_rates,
    list_samples,
)
from voicegateway.utils.percentiles import compute_percentiles

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from voicegateway.repository.calls_repository import CallRow
    from voicegateway.repository.node_samples_repository import NodeSampleRow

_logger = logging.getLogger(__name__)

# How much the window is widened on EACH side, in milliseconds. One scrape
# interval (``node_samples_worker_middleware`` polls at 15 s), for two reasons
# that both point the same way:
#
#   * a sample at ``t`` is the only observation there is of the node around
#     ``t``, and a 4-second call can easily fall between two of them. Without
#     padding, a short call correlates to nothing at all;
#   * the scrape host's clock and the clock that stamped the call are not the
#     same clock. A webhook ``created_at`` is whole seconds, an agent report is
#     millisecond, and neither is NTP-locked to the box running the scraper.
#
# It is a parameter on every entry point, and both bounds are reported on
# :class:`NodeWindow`, because "samples within 15 s of this call" is a different
# and weaker claim than "samples during this call", and the caller must be able
# to render which one it has.
DEFAULT_WINDOW_PAD_MS = 15_000

# How far BEFORE the padded window a read may reach for a predecessor sample.
# A counter rate needs a left-hand side: without one, the first sample inside the
# window has no rate at all (:func:`counter_rates` returns None for a first
# point), so a two-minute window would silently lose its opening point. Two
# scrape intervals is enough to survive one missed scrape.
#
# Bounded on purpose: diffing against a sample from an hour ago would spread that
# hour's increment across the window's first instant and publish it as a rate
# inside the window. Past this bound the first point stays unknown, which is the
# truth. Lead-in samples are used ONLY as that left-hand side -- they are never
# counted, summarized, or reported.
DEFAULT_LEAD_IN_MS = 30_000

# Below this many measured points, a percentile is a story about three numbers.
# D0 decision 3: render "max of N", never "p95".
MIN_PERCENTILE_SAMPLES = 10

# Closed set of :attr:`WindowCorrelation.status`.
#
#   'correlated'    at least one successful scrape of at least one node overlaps
#                   the window. Overlap only -- see the module docstring.
#   'scrape_failed' rows exist in the window but none of them succeeded. The
#                   nodes were being watched and the watching did not work, which
#                   is emphatically not "the nodes were fine".
#   'no_samples'    nothing was scraped in the window at all: no scrape worker,
#                   no configured target, or the window predates the trim. NOT a
#                   healthy node, and never rendered as zeros.
WINDOW_STATUSES: tuple[str, ...] = ("correlated", "scrape_failed", "no_samples")

# Closed set of ``peak_stat``. Naming the statistic beside the number is what
# stops "max of 3" being read as a p95.
PEAK_STATS: tuple[str, ...] = ("p95", "max_of_n", "not_measured")

_PERCENTILE = 95.0
_PERCENTILE_KEY = "p95"


@dataclass(frozen=True)
class NodeWindow:
    """The time window a correlation is computed over. Milliseconds, epoch.

    ``requested_*`` is what the caller asked about (a call's span, a ramp step);
    ``start_ms``/``end_ms`` are those bounds widened by ``pad_ms`` on each side.
    Both are carried so a renderer can say "within 15 s of" rather than "during",
    and so a padded correlation can never be presented as an exact one.
    """

    start_ms: int
    end_ms: int
    pad_ms: int
    requested_start_ms: int
    requested_end_ms: int


@dataclass(frozen=True)
class SampledTarget:
    """A ``(node, source)`` that has rows inside a window, and how many.

    "Sampled in the window", nothing more. It is not the node that served
    anything.
    """

    node: str
    source: str
    samples: int


@dataclass(frozen=True)
class GaugeSummary:
    """One point-in-time column over the window. Gauges are never diffed.

    ``samples`` counts rows whose value was actually present; a NULL is "not
    measured" and is excluded rather than treated as 0. With ``samples == 0``
    every number here is ``None`` and ``peak_stat`` is ``"not_measured"``.
    """

    column: str
    samples: int
    minimum: float | None
    maximum: float | None
    # Last measured value in the window, which is the one a saturation reading
    # (``filefd_allocated`` against ``filefd_maximum``) is compared at.
    latest: float | None
    peak: float | None
    peak_stat: str


@dataclass(frozen=True)
class CounterRateSummary:
    """One cumulative column over the window, as per-second rates.

    ``points`` are the in-window rate points; ``unknown_points`` are those whose
    rate is ``None``. Unknown covers the window's first point, a NULL at either
    end, a non-positive time delta, and a counter RESET -- and this module does
    not try to tell them apart, because :func:`counter_rates` is the only place
    that rule is implemented and a second copy of it is exactly the bug class
    that ships fabricated spikes.

    ``unknown_points == points`` means no rate in this window is sourceable. That
    is not 0/s.
    """

    column: str
    points: int
    unknown_points: int
    peak_per_second: float | None
    peak_stat: str


@dataclass(frozen=True)
class NodeSamplesInWindow:
    """What one ``(node, source)`` looked like while the window was open.

    Read as: *these scrapes overlap that window*. Not as: this node served the
    call.
    """

    node: str
    source: str
    samples: int
    ok_samples: int
    failed_samples: int
    # ``outcome`` -> count, so "5 rows, all timeouts" stays visible instead of
    # collapsing into an empty chart.
    outcomes: Mapping[str, int]
    first_sample_at_ms: int | None
    last_sample_at_ms: int | None
    gauges: Mapping[str, GaugeSummary]
    counters: Mapping[str, CounterRateSummary]
    # True when the read hit its row limit, so the summaries describe a PREFIX of
    # the window rather than the window. A truncated read that did not say so
    # would read as a complete one.
    truncated: bool


@dataclass(frozen=True)
class WindowCorrelation:
    """Nodes whose samples OVERLAP a window. Not the nodes that served a call.

    ``nodes_sampled`` is empty exactly when ``status == "no_samples"``; there is
    deliberately no entry with zeroed summaries for a node nobody scraped.
    """

    window: NodeWindow
    status: str
    nodes_sampled: list[NodeSamplesInWindow]


def window_of(
    start_ms: int, end_ms: int, *, pad_ms: int = DEFAULT_WINDOW_PAD_MS
) -> NodeWindow:
    """Build a window from an explicit span (a load ramp step, a chart brush).

    ``end_ms`` may equal ``start_ms`` (an instant); it may not precede it, which
    would only ever come from two clocks disagreeing, and a negative-width window
    would silently correlate to nothing while looking like a real query.
    """
    if end_ms < start_ms:
        raise ValueError(
            f"window end {end_ms} precedes start {start_ms}: a negative-width "
            "window correlates to nothing while looking like a real query"
        )
    if pad_ms < 0:
        raise ValueError(
            f"pad_ms must not be negative, got {pad_ms}: narrowing the window "
            "would drop samples that do overlap it"
        )
    return NodeWindow(
        start_ms=start_ms - pad_ms,
        end_ms=end_ms + pad_ms,
        pad_ms=pad_ms,
        requested_start_ms=start_ms,
        requested_end_ms=end_ms,
    )


def window_for_call(
    call: CallRow, *, pad_ms: int = DEFAULT_WINDOW_PAD_MS
) -> NodeWindow | None:
    """The window a call was open for, or ``None`` if it has no closed span.

    ONLY the two timestamps are read off the call. Nothing else about it reaches
    the node query, and no node is recorded against it: the call supplies a time
    range and gets back what was happening on the boxes then.

    ``None`` when ``started_at_ms`` or ``ended_at_ms`` is NULL -- an INVITE that
    never produced a room, or a call still in flight (or one whose
    ``room_finished`` webhook never arrived). Substituting "now" for a missing end
    would invent a span, and the overlap set would silently depend on when the
    page was loaded. A caller that genuinely wants the in-flight case builds the
    window with :func:`window_of` and owns that claim.
    """
    if call.started_at_ms is None or call.ended_at_ms is None:
        return None
    if call.ended_at_ms < call.started_at_ms:
        # ``upsert_call`` already refuses to derive ``duration_ms`` from an
        # inverted span; correlating over one would be the same clock
        # disagreement expressed as a query.
        _logger.debug(
            "call %s has ended_at_ms %d before started_at_ms %d; no window",
            call.id,
            call.ended_at_ms,
            call.started_at_ms,
        )
        return None
    return window_of(call.started_at_ms, call.ended_at_ms, pad_ms=pad_ms)


def _validate_columns(
    requested: Sequence[str] | None, allowed: frozenset[str], kind: str
) -> tuple[str, ...]:
    """Resolve a caller's column list against a whitelist, sorted for stability.

    ``None`` means every column of that kind. An unknown or mis-kinded name
    raises rather than being ignored: a gauge asked for as a counter would
    otherwise silently vanish from the result, which reads as "not measured".
    The whitelist is also what keeps a caller-supplied string out of the query --
    columns are read off already-loaded rows by name.
    """
    if requested is None:
        return tuple(sorted(allowed))
    unknown = [name for name in requested if name not in allowed]
    if unknown:
        raise ValueError(
            f"{sorted(unknown)} are not node_samples {kind} columns; {kind} "
            f"columns are {sorted(allowed)}"
        )
    return tuple(sorted(set(requested)))


def _peak(values: Sequence[float]) -> tuple[float | None, str]:
    """``(value, statistic)`` for a series, honest about how few points it had.

    p95 only from :data:`MIN_PERCENTILE_SAMPLES` points up; below that the
    maximum, labelled ``max_of_n`` so nobody reads it as a percentile; ``None``
    and ``not_measured`` when there is nothing to summarize.
    """
    if not values:
        return None, "not_measured"
    if len(values) < MIN_PERCENTILE_SAMPLES:
        return max(values), "max_of_n"
    return compute_percentiles(list(values), [_PERCENTILE])[_PERCENTILE_KEY], "p95"


def _gauge_summary(column: str, rows: Sequence[NodeSampleRow]) -> GaugeSummary:
    """Summarize one gauge over the in-window rows. NULLs are skipped, not zeroed."""
    values = [
        float(value) for row in rows if (value := getattr(row, column)) is not None
    ]
    peak, peak_stat = _peak(values)
    return GaugeSummary(
        column=column,
        samples=len(values),
        minimum=min(values) if values else None,
        maximum=max(values) if values else None,
        # ``rows`` is oldest-first (``list_samples`` guarantees it), so the last
        # measured value is the newest one in the window.
        latest=values[-1] if values else None,
        peak=peak,
        peak_stat=peak_stat,
    )


def _counter_summary(
    column: str, rows: Sequence[NodeSampleRow], lead_in: int
) -> CounterRateSummary:
    """Summarize one counter over the window, diffed by :func:`counter_rates`.

    ``rows`` begins with ``lead_in`` rows (at most one) from BEFORE the window,
    which exist only to give the window's first sample a predecessor to diff
    against. They are fed to the rate computation and then dropped: the rates are
    1:1 with the points in order, so the first ``lead_in`` results are discarded
    and only in-window points are summarized.

    The reset rule is not re-implemented here, or anywhere else. A ``None`` rate
    is counted as unknown and excluded from the peak, so a livekit-server restart
    inside the window lowers confidence instead of producing a spike.
    """
    points: list[tuple[int, float | None]] = [
        (row.at_ms, None if (value := getattr(row, column)) is None else float(value))
        for row in rows
    ]
    rates = counter_rates(points)[lead_in:]
    known = [rate.per_second for rate in rates if rate.per_second is not None]
    peak, peak_stat = _peak(known)
    return CounterRateSummary(
        column=column,
        points=len(rates),
        unknown_points=len(rates) - len(known),
        peak_per_second=peak,
        peak_stat=peak_stat,
    )


def _outcome_counts(rows: Iterable[NodeSampleRow]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.outcome] = counts.get(row.outcome, 0) + 1
    return counts


async def list_nodes_sampled_in_window(
    db: AsyncSession,
    *,
    window: NodeWindow,
    node: str | None = None,
    source: str | None = None,
) -> list[SampledTarget]:
    """Which ``(node, source)`` targets have samples inside ``window``.

    "Sampled in the window" is the whole claim. A node absent from this list was
    not observed then -- it may have been serving every call in the deployment
    with no scraper pointed at it -- so an empty list means "we did not look",
    never "the fleet was idle".
    """
    conditions = ["at_ms >= :start_ms", "at_ms <= :end_ms"]
    params: dict[str, Any] = {"start_ms": window.start_ms, "end_ms": window.end_ms}
    if node is not None:
        conditions.append("node = :node")
        params["node"] = node
    if source is not None:
        conditions.append("source = :source")
        params["source"] = source
    rows = (
        await db.execute(
            text(
                "SELECT node, source, COUNT(*) FROM node_samples "
                f"WHERE {' AND '.join(conditions)} "
                "GROUP BY node, source ORDER BY node, source"
            ),
            params,
        )
    ).fetchall()
    return [
        SampledTarget(node=str(row[0]), source=str(row[1]), samples=int(row[2] or 0))
        for row in rows
    ]


async def correlate_window(
    db: AsyncSession,
    *,
    window: NodeWindow,
    node: str | None = None,
    source: str | None = None,
    gauges: Sequence[str] | None = None,
    counters: Sequence[str] | None = None,
    lead_in_ms: int = DEFAULT_LEAD_IN_MS,
    limit: int = DEFAULT_READ_LIMIT,
) -> WindowCorrelation:
    """Node samples that OVERLAP ``window``, summarized per ``(node, source)``.

    This is a time-window correlation and nothing else. It cannot say a node
    served a call, because nothing server-side can: layer 7 has no per-call
    identity, which is why ``node_samples`` has no ``call_id`` and this function
    takes a window rather than a call id.

    Two reads per target -- the window itself, plus at most one preceding sample
    to give the counter diff a left-hand side -- and then every summary is
    computed in memory from those rows, reusing :func:`counter_rates` directly
    rather than re-querying per column, which on ten targets would be ninety
    extra round trips for the same numbers. The two reads are separate so that
    ``limit`` bounds the samples IN the window: a lead-in row counted against it
    could otherwise consume the budget and make a truncated read look like an
    unscraped window.

    ``gauges`` / ``counters`` default to every column of that kind; a summary is
    returned for each requested column even when nothing measured it, so an
    absent series reads as ``not_measured`` rather than disappearing.
    """
    if lead_in_ms < 0:
        raise ValueError(
            f"lead_in_ms must not be negative, got {lead_in_ms}: a negative "
            "lead-in would diff the first in-window sample against a later one"
        )
    gauge_columns = _validate_columns(gauges, GAUGE_COLUMNS, "gauge")
    counter_columns = _validate_columns(counters, COUNTER_COLUMNS, "counter")

    targets = await list_nodes_sampled_in_window(
        db, window=window, node=node, source=source
    )
    sampled: list[NodeSamplesInWindow] = []
    for target in targets:
        in_window = await list_samples(
            db,
            node=target.node,
            source=target.source,
            since_ms=window.start_ms,
            until_ms=window.end_ms,
            limit=limit,
        )
        if not in_window:
            # The discovery query saw rows here, so this is a trim landing
            # between the two reads. Nothing to summarize, and inventing an entry
            # would name a target with no observations behind it.
            _logger.debug(
                "node %s/%s lost its samples in [%d, %d] between reads",
                target.node,
                target.source,
                window.start_ms,
                window.end_ms,
            )
            continue
        # At most ONE row from before the window, used only as the left-hand side
        # of the first in-window counter diff: never counted, never summarized,
        # never reported. ``list_samples`` is oldest-first, so the newest
        # available predecessor is the last one; if that read were itself
        # truncated the row taken would be older, but still inside ``lead_in_ms``,
        # which is the whole guarantee.
        lead_in_rows = (
            await list_samples(
                db,
                node=target.node,
                source=target.source,
                since_ms=window.start_ms - lead_in_ms,
                until_ms=window.start_ms - 1,
                limit=limit,
            )
            if lead_in_ms > 0
            else []
        )
        predecessor = lead_in_rows[-1:]
        rows = predecessor + in_window
        lead_in = len(predecessor)
        outcomes = _outcome_counts(in_window)
        ok_samples = outcomes.get("ok", 0)
        sampled.append(
            NodeSamplesInWindow(
                node=target.node,
                source=target.source,
                samples=len(in_window),
                ok_samples=ok_samples,
                failed_samples=len(in_window) - ok_samples,
                outcomes=outcomes,
                first_sample_at_ms=in_window[0].at_ms,
                last_sample_at_ms=in_window[-1].at_ms,
                gauges={
                    column: _gauge_summary(column, in_window)
                    for column in gauge_columns
                },
                counters={
                    column: _counter_summary(column, rows, lead_in)
                    for column in counter_columns
                },
                truncated=len(in_window) >= limit,
            )
        )

    if not sampled:
        status = "no_samples"
    elif any(entry.ok_samples for entry in sampled):
        status = "correlated"
    else:
        # Rows exist and every one of them is a failed scrape. The nodes were
        # watched, the watching did not work, and that is not a clean bill.
        status = "scrape_failed"
    return WindowCorrelation(window=window, status=status, nodes_sampled=sampled)


async def correlate_call_window(
    db: AsyncSession,
    *,
    call: CallRow,
    pad_ms: int = DEFAULT_WINDOW_PAD_MS,
    node: str | None = None,
    source: str | None = None,
    gauges: Sequence[str] | None = None,
    counters: Sequence[str] | None = None,
    lead_in_ms: int = DEFAULT_LEAD_IN_MS,
    limit: int = DEFAULT_READ_LIMIT,
) -> WindowCorrelation | None:
    """Node samples overlapping the window a call was open for, or ``None``.

    ``None`` means the CALL has no closed window (see :func:`window_for_call`) --
    distinct from a window that produced no samples, which comes back as a
    :class:`WindowCorrelation` with ``status == "no_samples"``.

    The call contributes two timestamps and nothing else. Nothing is written, no
    node is recorded against the call, and the result must never be rendered as
    "this call ran on these nodes": it is "these nodes were being scraped while
    this call was open", which for a multi-node deployment is usually several
    nodes at once, and for a deployment with no scraper is none.
    """
    window = window_for_call(call, pad_ms=pad_ms)
    if window is None:
        return None
    return await correlate_window(
        db,
        window=window,
        node=node,
        source=source,
        gauges=gauges,
        counters=counters,
        lead_in_ms=lead_in_ms,
        limit=limit,
    )


__all__ = [
    "DEFAULT_LEAD_IN_MS",
    "DEFAULT_WINDOW_PAD_MS",
    "MIN_PERCENTILE_SAMPLES",
    "PEAK_STATS",
    "WINDOW_STATUSES",
    "CounterRateSummary",
    "GaugeSummary",
    "NodeSamplesInWindow",
    "NodeWindow",
    "SampledTarget",
    "WindowCorrelation",
    "correlate_call_window",
    "correlate_window",
    "list_nodes_sampled_in_window",
    "window_for_call",
    "window_of",
]
