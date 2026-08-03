"""Async repo for the ``node_samples`` table (layer 7 scrapes, read-time diffed).

Four properties this module exists to hold:

* **Append, never upsert.** A sample is an observation, not an entity: two
  scrapes of one node are two facts about two instants. There is no natural key
  to conflict on, so unlike every other repository here this one inserts. (The
  select-then-update rule exists for tables whose nullable unique keys make a
  native ON CONFLICT duplicate rows; there is no such key here.)

* **Counters are diffed at READ time, and a decrease yields NULL.** A
  livekit-server or host restart zeroes every ``_total``. Diffing across that
  boundary produces a huge negative rate, and clamping it to 0 is worse than the
  negative: 0 renders as "this node carried no traffic" on the one chart an
  operator reads to explain a knee. The truth is that the increment between the
  two samples is *unknowable* -- the counter may have climbed for a while before
  it zeroed -- and NULL is the only value that says so. See
  :func:`counter_rates`.

* **NULL is never repaired.** A column is NULL because the series was absent,
  the scrape failed, or the release renamed the metric. Nothing here
  interpolates, carries a value forward, or substitutes 0. A caller rendering
  these must suppress the series and label it not measured.

* **The table trims itself.** ~10 nodes at 15 s is ~57k rows/day, on the same
  SQLite writer that absorbs webhook bursts. :func:`trim_older_than` is called
  unconditionally by the scrape worker on every tick; the per-project retention
  pass is the second line of defence, not the first (it is opt-in and it
  discovers projects from ``requests``/``sessions``, which a node-only
  deployment never populates).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy import select, text

from voicegateway.models.node_sample_model import NodeSample

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_logger = logging.getLogger(__name__)

DEFAULT_PROJECT = "default"

# How many samples one read returns at most, so a caller cannot pull a week of
# 15-second scrapes into memory by omitting a window.
DEFAULT_READ_LIMIT = 5_000

# How many rows one trim batch deletes. Matches the retention worker's default:
# a bounded delete never holds a long write lock, which keeps the pass friendly
# to the shared SQLite writer.
DEFAULT_TRIM_BATCH = 500

# Cumulative counters: monotonic between restarts, meaningless as an absolute,
# read as a rate via :func:`counter_rates`.
COUNTER_COLUMNS: frozenset[str] = frozenset(
    {
        "packets_total",
        "packet_bytes_total",
        "sip_invite_requests_raw_total",
        "sip_invite_requests_total",
        "sip_invite_accepted_total",
        "sip_calls_terminated_total",
        "cpu_seconds_total",
        "cpu_idle_seconds_total",
        # Histogram _sum and _count are both cumulative, so a rate over them is
        # the only meaningful read. The two bucket counts are cumulative too.
        "sip_join_sec_sum",
        "sip_join_sec_count",
        "sip_join_le1_count",
        "sip_join_le5_count",
        "sip_check_sec_sum",
        "sip_check_sec_count",
        "session_start_time_ms_sum",
        "session_start_time_ms_count",
        "sip_rtp_packets_recv",
        "sip_rtp_packets_send",
        "process_cpu_seconds_total",
        "udp_no_ports_total",
        # Cumulative since boot. Only an INCREASE inside a window is a kill.
        "vmstat_oom_kill",
        # Redis. Cumulative since the server started, so only a rate is
        # meaningful: an absolute "3 rejected connections" says nothing
        # about whether any were rejected during THIS window.
        "redis_rejected_connections_total",
        "redis_evicted_keys_total",
        # Cumulative since DRIVER reset, not since the run started. One live SIP
        # node carries 9613 on bw_in from before the engagement while its twin
        # carries 0, so only the delta over the window is attributable; an
        # absolute would fail a node for throttling it never suffered here.
        "ethtool_bw_in_allowance_exceeded",
        "ethtool_bw_out_allowance_exceeded",
        "ethtool_pps_allowance_exceeded",
        "ethtool_conntrack_allowance_exceeded",
        "ethtool_linklocal_allowance_exceeded",
        # The throughput the allowances cap. Read as a rate; the absolute is
        # bytes since boot and says nothing about this window.
        "network_receive_bytes_total",
        "network_transmit_bytes_total",
    }
)

# Point-in-time values: read exactly as scraped, never diffed.
GAUGE_COLUMNS: frozenset[str] = frozenset(
    {
        "rooms",
        "participants",
        "sip_calls_active",
        "filefd_allocated",
        "filefd_maximum",
        "load1",
        "memory_available_bytes",
        "memory_total_bytes",
        # Go runtime. Gauges: what the process holds right now, never diffed.
        "heap_inuse_bytes",
        "go_goroutines",
        # Per-process rlimit pair, the ceiling a service actually hits.
        "process_open_fds",
        "process_max_fds",
        "sip_available",
        "sip_node_cpu_load",
        # A constant per process life, not cumulative: it is the instant the
        # process started, and it changes only across a restart.
        "process_start_time_seconds",
        "process_resident_memory_bytes",
        "sockstat_udp_inuse",
        # Named _total by the exporter but behaving as gauges: current counts,
        # not cumulative. Putting them in COUNTER_COLUMNS would have
        # read_counter_rate diff a value that already is the answer.
        "track_published_total",
        "track_subscribed_total",
        "psrpc_stream_count",
        # Derived from node_filefd_maximum rather than scraped, but still an
        # observation about one scrape. 1 unbounded, 0 bounded, NULL unmeasured.
        "filefd_maximum_unbounded",
        # Redis point-in-time state.
        "redis_up",
        "redis_memory_used_bytes",
        "redis_memory_max_bytes",
        "redis_memory_max_unbounded",
        "redis_blocked_clients",
        # Health probe. Not scraped from an exposition, but still an
        # observation about one sampling tick, read exactly as recorded.
        "health_ok",
        "health_status_code",
        "health_timed_out",
        # Media port headroom. Both are point-in-time: in_use is the count of
        # UDP sockets bound inside the range right now, and total is the
        # declared size of that range, republished on every scrape so a ratio is
        # taken against the range in force at that instant. total NULL means the
        # headroom is UNKNOWN, never "assume the usual range": a guessed
        # denominator invents a saturation percentage nobody measured.
        "media_ports_total",
        "media_ports_in_use",
    }
)

VALUE_COLUMNS: frozenset[str] = COUNTER_COLUMNS | GAUGE_COLUMNS

# Value columns computed FROM a scrape rather than read off one. They are real
# observations and belong in VALUE_COLUMNS, but they are not series, so they are
# counted out of ``series_found``: that number answers "how many of the series
# expected for this source did the target actually expose", and a derived
# marker would inflate it into a claim about the target.
DERIVED_COLUMNS: frozenset[str] = frozenset(
    {
        "filefd_maximum_unbounded",
        "redis_memory_max_unbounded",
        # The health columns come from an HTTP probe, not from the
        # exposition, so counting them would inflate series_found into a
        # claim about what the metrics target exposed.
        "health_ok",
        "health_status_code",
        "health_timed_out",
    }
)

# Columns stored as integers. Everything else in VALUE_COLUMNS is a float.
# Prometheus exposition is float-typed on the wire, so an integer column is
# truncated on the way in (see :func:`_coerce`).
_INT_COLUMNS: frozenset[str] = frozenset(
    {
        "rooms",
        "participants",
        "packets_total",
        "packet_bytes_total",
        "sip_calls_active",
        "sip_invite_requests_raw_total",
        "sip_invite_requests_total",
        "sip_invite_accepted_total",
        "sip_calls_terminated_total",
        "filefd_allocated",
        "filefd_maximum",
        "memory_available_bytes",
        "memory_total_bytes",
        "heap_inuse_bytes",
        "go_goroutines",
        # Integer columns. Anything omitted here is stored as a float, and
        # anything named here that is genuinely fractional would be truncated,
        # so sip_join_sec_sum / sip_check_sec_sum / sip_node_cpu_load /
        # process_start_time_seconds / process_cpu_seconds_total are all
        # deliberately ABSENT: they are seconds and load averages.
        "sip_join_sec_count",
        "sip_join_le1_count",
        "sip_join_le5_count",
        "sip_check_sec_count",
        "session_start_time_ms_sum",
        "session_start_time_ms_count",
        "process_open_fds",
        "process_max_fds",
        "sip_available",
        "sip_rtp_packets_recv",
        "sip_rtp_packets_send",
        "process_resident_memory_bytes",
        "sockstat_udp_inuse",
        "udp_no_ports_total",
        "vmstat_oom_kill",
        "track_published_total",
        "track_subscribed_total",
        "psrpc_stream_count",
        "filefd_maximum_unbounded",
        # Redis. Counts, byte totals and 0/1 markers, none of them fractional.
        "redis_up",
        "redis_rejected_connections_total",
        "redis_memory_used_bytes",
        "redis_memory_max_bytes",
        "redis_memory_max_unbounded",
        "redis_blocked_clients",
        "redis_evicted_keys_total",
        # Health probe: two 0/1 markers and an HTTP status code.
        "health_ok",
        "health_status_code",
        "health_timed_out",
        # Port counts and byte/event counters. All integral: a fractional port
        # or a fractional throttling event is not a thing the exposition can
        # mean, so truncation here loses nothing.
        "media_ports_total",
        "media_ports_in_use",
        "ethtool_bw_in_allowance_exceeded",
        "ethtool_bw_out_allowance_exceeded",
        "ethtool_pps_allowance_exceeded",
        "ethtool_conntrack_allowance_exceeded",
        "ethtool_linklocal_allowance_exceeded",
        "network_receive_bytes_total",
        "network_transmit_bytes_total",
    }
)

# Widest value a 64-bit column takes. ``node_filefd_maximum`` is commonly
# 9223372036854775807, which round-trips through float64 as 9.223372036854776e18
# and truncates to 2**63 -- one past the limit, which SQLite raises on and
# PostgreSQL rejects. A value that does not fit is dropped (stored NULL, "not
# measured"), never clamped: a clamped ceiling would silently become a real
# reading of a saturation headroom chart.
_INT64_MAX = 2**63 - 1
_INT64_MIN = -(2**63)

# Columns already reported as overflowing, so the warning is not repeated on
# every scrape. Process-wide and deliberately never cleared: the condition is a
# property of the host, not of one tick, and a "reset" would only restore the
# flood. Bounded by the number of value columns, so it cannot grow.
_OVERFLOW_WARNED: set[str] = set()


@dataclass(frozen=True)
class NodeSampleInput:
    """One scrape, as produced by the worker and handed to :func:`insert_samples`.

    ``values`` carries only the series that were actually present; any column
    left out stays NULL, which is what a reader must render as not measured.
    """

    node: str
    source: str
    at_ms: int
    outcome: str
    project: str = DEFAULT_PROJECT
    series_found: int | None = None
    values: Mapping[str, float | None] = field(default_factory=dict)


@dataclass(frozen=True)
class NodeSampleRow:
    """One ``node_samples`` row as served to readers."""

    id: int | None
    node: str
    source: str
    at_ms: int
    project: str
    outcome: str
    series_found: int | None
    rooms: int | None
    participants: int | None
    packets_total: int | None
    packet_bytes_total: int | None
    sip_calls_active: int | None
    sip_invite_requests_raw_total: int | None
    sip_invite_requests_total: int | None
    sip_invite_accepted_total: int | None
    sip_calls_terminated_total: int | None
    filefd_allocated: int | None
    filefd_maximum: int | None
    load1: float | None
    cpu_seconds_total: float | None
    cpu_idle_seconds_total: float | None
    memory_available_bytes: int | None
    memory_total_bytes: int | None
    heap_inuse_bytes: int | None
    go_goroutines: int | None
    sip_join_sec_sum: float | None
    sip_join_sec_count: int | None
    sip_join_le1_count: int | None
    sip_join_le5_count: int | None
    sip_check_sec_sum: float | None
    sip_check_sec_count: int | None
    session_start_time_ms_sum: int | None
    session_start_time_ms_count: int | None
    process_open_fds: int | None
    process_max_fds: int | None
    sip_available: int | None
    sip_node_cpu_load: float | None
    sip_rtp_packets_recv: int | None
    sip_rtp_packets_send: int | None
    process_start_time_seconds: float | None
    process_cpu_seconds_total: float | None
    process_resident_memory_bytes: int | None
    sockstat_udp_inuse: int | None
    udp_no_ports_total: int | None
    track_published_total: int | None
    track_subscribed_total: int | None
    psrpc_stream_count: int | None
    filefd_maximum_unbounded: int | None

    # Redis and health. Nullable like every value column: NULL means
    # nobody measured, never zero.
    redis_up: int | None
    redis_rejected_connections_total: int | None
    redis_memory_used_bytes: int | None
    redis_memory_max_bytes: int | None
    redis_memory_max_unbounded: int | None
    redis_blocked_clients: int | None
    redis_evicted_keys_total: int | None
    health_ok: int | None
    health_status_code: int | None
    health_timed_out: int | None
    vmstat_oom_kill: int | None

    # Media port and network headroom. media_ports_total is the declared range
    # size, so NULL here makes the headroom UNKNOWN rather than assumable, and
    # the ethtool counters are cumulative since driver reset, so a reader must
    # take a delta over its window rather than the value it is handed.
    media_ports_total: int | None
    media_ports_in_use: int | None
    ethtool_bw_in_allowance_exceeded: int | None
    ethtool_bw_out_allowance_exceeded: int | None
    ethtool_pps_allowance_exceeded: int | None
    ethtool_conntrack_allowance_exceeded: int | None
    ethtool_linklocal_allowance_exceeded: int | None
    network_receive_bytes_total: int | None
    network_transmit_bytes_total: int | None


@dataclass(frozen=True)
class CounterRate:
    """A per-second rate at ``at_ms``, or ``None`` when it is unknowable.

    ``per_second is None`` for the first sample of a series (nothing to diff
    against), for a sample whose column is NULL at either end, and -- the case
    this type exists for -- for a counter that went BACKWARDS. None is not zero
    and not a negative: it is "we cannot say".
    """

    at_ms: int
    per_second: float | None


# Tolerance for an idle rate that rounds just above its capacity rate. The two
# come from the same scrape of the same counter, so a genuine excess means the
# pair does not describe one machine; this only absorbs float division noise.
_UTILISATION_EPSILON = 1e-9


@dataclass(frozen=True)
class UtilisationPoint:
    """The busy fraction at ``at_ms``, or ``None`` when it is unknowable.

    ``fraction`` is 0.0 to 1.0. ``None`` is not zero: a fully idle machine reads
    0.0, while a machine nobody could measure reads ``None``, and rendering the
    second as the first is a clean bill of health nobody earned.
    """

    at_ms: int
    fraction: float | None


def _row(sample: NodeSample) -> NodeSampleRow:
    return NodeSampleRow(
        id=sample.id,
        node=sample.node,
        source=sample.source,
        at_ms=sample.at_ms,
        project=sample.project,
        outcome=sample.outcome,
        series_found=sample.series_found,
        rooms=sample.rooms,
        participants=sample.participants,
        packets_total=sample.packets_total,
        packet_bytes_total=sample.packet_bytes_total,
        sip_calls_active=sample.sip_calls_active,
        sip_invite_requests_raw_total=sample.sip_invite_requests_raw_total,
        sip_invite_requests_total=sample.sip_invite_requests_total,
        sip_invite_accepted_total=sample.sip_invite_accepted_total,
        sip_calls_terminated_total=sample.sip_calls_terminated_total,
        filefd_allocated=sample.filefd_allocated,
        filefd_maximum=sample.filefd_maximum,
        load1=sample.load1,
        cpu_seconds_total=sample.cpu_seconds_total,
        cpu_idle_seconds_total=sample.cpu_idle_seconds_total,
        memory_available_bytes=sample.memory_available_bytes,
        memory_total_bytes=sample.memory_total_bytes,
        heap_inuse_bytes=sample.heap_inuse_bytes,
        go_goroutines=sample.go_goroutines,
        sip_join_sec_sum=sample.sip_join_sec_sum,
        sip_join_sec_count=sample.sip_join_sec_count,
        sip_join_le1_count=sample.sip_join_le1_count,
        sip_join_le5_count=sample.sip_join_le5_count,
        sip_check_sec_sum=sample.sip_check_sec_sum,
        sip_check_sec_count=sample.sip_check_sec_count,
        session_start_time_ms_sum=sample.session_start_time_ms_sum,
        session_start_time_ms_count=sample.session_start_time_ms_count,
        process_open_fds=sample.process_open_fds,
        process_max_fds=sample.process_max_fds,
        sip_available=sample.sip_available,
        sip_node_cpu_load=sample.sip_node_cpu_load,
        sip_rtp_packets_recv=sample.sip_rtp_packets_recv,
        sip_rtp_packets_send=sample.sip_rtp_packets_send,
        process_start_time_seconds=sample.process_start_time_seconds,
        process_cpu_seconds_total=sample.process_cpu_seconds_total,
        process_resident_memory_bytes=sample.process_resident_memory_bytes,
        sockstat_udp_inuse=sample.sockstat_udp_inuse,
        udp_no_ports_total=sample.udp_no_ports_total,
        track_published_total=sample.track_published_total,
        track_subscribed_total=sample.track_subscribed_total,
        psrpc_stream_count=sample.psrpc_stream_count,
        filefd_maximum_unbounded=sample.filefd_maximum_unbounded,
        redis_up=sample.redis_up,
        redis_rejected_connections_total=sample.redis_rejected_connections_total,
        redis_memory_used_bytes=sample.redis_memory_used_bytes,
        redis_memory_max_bytes=sample.redis_memory_max_bytes,
        redis_memory_max_unbounded=sample.redis_memory_max_unbounded,
        redis_blocked_clients=sample.redis_blocked_clients,
        redis_evicted_keys_total=sample.redis_evicted_keys_total,
        health_ok=sample.health_ok,
        health_status_code=sample.health_status_code,
        health_timed_out=sample.health_timed_out,
        vmstat_oom_kill=sample.vmstat_oom_kill,
        media_ports_total=sample.media_ports_total,
        media_ports_in_use=sample.media_ports_in_use,
        ethtool_bw_in_allowance_exceeded=sample.ethtool_bw_in_allowance_exceeded,
        ethtool_bw_out_allowance_exceeded=sample.ethtool_bw_out_allowance_exceeded,
        ethtool_pps_allowance_exceeded=sample.ethtool_pps_allowance_exceeded,
        ethtool_conntrack_allowance_exceeded=(
            sample.ethtool_conntrack_allowance_exceeded
        ),
        ethtool_linklocal_allowance_exceeded=(
            sample.ethtool_linklocal_allowance_exceeded
        ),
        network_receive_bytes_total=sample.network_receive_bytes_total,
        network_transmit_bytes_total=sample.network_transmit_bytes_total,
    )


def _coerce(column: str, value: float | None) -> int | float | None:
    """Storage value for one scraped series, or None to leave the column NULL.

    Refuses rather than mangles: a NaN or an infinity is not a measurement, and
    an integer outside the 64-bit range would either raise on insert (SQLite) or
    be rejected (PostgreSQL). Both store NULL and log, because a wrong number on
    a saturation chart is worse than an admitted gap.
    """
    if value is None:
        return None
    if not math.isfinite(value):
        _logger.debug("node_samples.%s dropped: value %r is not finite", column, value)
        return None
    if column not in _INT_COLUMNS:
        return float(value)
    as_int = int(value)
    if not (_INT64_MIN <= as_int <= _INT64_MAX):
        # Logged ONCE per column rather than per scrape. On a host with an
        # unbounded fs.file-max this fires on every tick, four times a minute,
        # for as long as the collector runs, and a 24 hour soak buries every
        # real warning under ~5,700 copies of one that is not news.
        #
        # It was the right warning before filefd_maximum_unbounded existed. Now
        # the sentinel records the same fact explicitly and durably, on the row,
        # where a reader can act on it. The first occurrence is still a warning
        # because a column silently going NULL deserves to be seen once;
        # repeats drop to debug.
        if column in _OVERFLOW_WARNED:
            _logger.debug(
                "node_samples.%s dropped again: %r does not fit a 64-bit column",
                column,
                value,
            )
        else:
            _OVERFLOW_WARNED.add(column)
            _logger.warning(
                "node_samples.%s dropped: %r does not fit a 64-bit column; "
                "storing NULL rather than a clamped value that would read as a "
                "measurement. Further occurrences for this column log at debug",
                column,
                value,
            )
        return None
    return as_int


async def insert_samples(db: AsyncSession, samples: Sequence[NodeSampleInput]) -> int:
    """Append one row per scrape; return how many were written.

    One commit for the whole pass: a scrape tick is a set of observations taken
    at one instant, and committing per target would multiply the write-lock
    acquisitions on a SQLite file that is also absorbing webhooks.

    An unknown key in ``values`` raises. It can only come from a metric map that
    names a column the model does not have, which is a programming error that
    would otherwise be silently dropped and show up as an always-empty chart.
    """
    if not samples:
        return 0
    for sample in samples:
        row = NodeSample(
            node=sample.node,
            source=sample.source,
            at_ms=sample.at_ms,
            project=sample.project,
            outcome=sample.outcome,
            series_found=sample.series_found,
        )
        stored = 0
        for column, value in sample.values.items():
            if column not in VALUE_COLUMNS:
                raise ValueError(
                    f"unknown node_samples value column {column!r}; known columns "
                    f"are {sorted(VALUE_COLUMNS)}"
                )
            coerced = _coerce(column, value)
            setattr(row, column, coerced)
            if coerced is not None and column not in DERIVED_COLUMNS:
                stored += 1
        if sample.series_found is not None:
            row.series_found = _reconciled_series_found(sample, stored)
        db.add(row)
    await db.commit()
    return len(samples)


def _reconciled_series_found(sample: NodeSampleInput, stored: int) -> int:
    """What ``series_found`` must say once coercion has had its turn.

    The caller counts a series when it MATCHES the exposition, which is before
    this module gets to refuse it. A value that is non-finite, or that does not
    survive the trip through a 64-bit column, is dropped here and the caller's
    count never hears about it: the row then claims one more measurement than it
    holds, and it is the columns nobody can read that go missing, so the
    overstatement is invisible in exactly the case it matters.

    A derived marker is excluded because it is not a series. Counting it would
    make the number say a target exposed something it never did.
    """
    claimed = sample.series_found
    if claimed is not None and claimed != stored:
        _logger.debug(
            "node_samples series_found for %s/%s corrected %d -> %d: %d value(s) "
            "were refused at coercion",
            sample.node,
            sample.source,
            claimed,
            stored,
            claimed - stored,
        )
    return stored


async def list_samples(
    db: AsyncSession,
    *,
    node: str,
    source: str,
    since_ms: int | None = None,
    until_ms: int | None = None,
    limit: int = DEFAULT_READ_LIMIT,
) -> list[NodeSampleRow]:
    """One node's samples from one source, OLDEST first.

    Ascending, unlike every "newest first" listing elsewhere, because the caller
    is diffing a counter and a diff is only meaningful in time order. ``id`` is
    the stable tiebreak for two samples that share a timestamp.
    """
    stmt = select(NodeSample).where(
        NodeSample.node == node,  # type: ignore[arg-type]
        NodeSample.source == source,  # type: ignore[arg-type]
    )
    if since_ms is not None:
        stmt = stmt.where(NodeSample.at_ms >= since_ms)  # type: ignore[arg-type]
    if until_ms is not None:
        stmt = stmt.where(NodeSample.at_ms <= until_ms)  # type: ignore[arg-type]
    stmt = stmt.order_by(
        NodeSample.at_ms.asc(),  # type: ignore[attr-defined]
        NodeSample.id.asc(),  # type: ignore[union-attr]
    ).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return [_row(sample) for sample in rows]


def counter_rates(points: Sequence[tuple[int, float | None]]) -> list[CounterRate]:
    """Per-second rates from ``(at_ms, cumulative_value)`` pairs, oldest first.

    THE counter-reset rule, and the only place it is implemented:

    * The first point has no predecessor, so its rate is None.
    * Either end NULL (the series was absent, or the scrape failed) makes the
      increment unknown, so None.
    * ``current < previous`` is a RESET -- a livekit-server restart zeroes every
      ``_total``, and so does a reboot for ``node_cpu_seconds_total``. The
      increment across that boundary is unknowable: the counter may have climbed
      for most of the interval before it zeroed. The rate is None. **Not 0**
      (which renders as an idle node, i.e. a clean bill of health at exactly the
      moment something restarted) and **not the raw negative** (which renders as
      a spike pointing the wrong way).
    * A non-positive time delta (duplicate timestamp, clock stepped backwards)
      makes the division meaningless, so None.

    ``current == previous`` is NOT a reset: a counter that did not move is a
    genuine 0.0/s, and that is what is returned.
    """
    rates: list[CounterRate] = []
    previous: tuple[int, float] | None = None
    for at_ms, value in points:
        if value is None:
            rates.append(CounterRate(at_ms=at_ms, per_second=None))
            # A gap breaks the chain: the next sample cannot be diffed against a
            # value we never saw.
            previous = None
            continue
        if previous is None:
            rates.append(CounterRate(at_ms=at_ms, per_second=None))
            previous = (at_ms, value)
            continue
        prev_at_ms, prev_value = previous
        delta_seconds = (at_ms - prev_at_ms) / 1000.0
        if value < prev_value or delta_seconds <= 0:
            rates.append(CounterRate(at_ms=at_ms, per_second=None))
        else:
            rates.append(
                CounterRate(
                    at_ms=at_ms, per_second=(value - prev_value) / delta_seconds
                )
            )
        previous = (at_ms, value)
    return rates


def utilisation_points(
    capacity: Sequence[CounterRate],
    idle: Sequence[CounterRate],
) -> list[UtilisationPoint]:
    """Busy fraction per instant, from a total rate and its idle subset.

    Written for the cpu pair and general in the same shape: ``cpu_seconds_total``
    is summed over every cpu AND every mode, while ``cpu_idle_seconds_total`` is
    that same metric restricted to ``mode="idle"``. So the total rate IS the core
    count (each core accrues one cpu-second per second across all modes), and
    ``1 - idle/total`` is utilisation exactly, with no core count needed and no
    assumption about machine size.

    PAIRED BY ``at_ms``, never by position. The two series are read separately,
    so a NULL in one column can leave them different lengths, and zipping them
    positionally would silently compare a rate at one instant against a rate at
    another. An instant present in only one series is not a measurement.

    This lives beside :func:`counter_rates` on purpose. The reset rule is
    implemented there once, and this function consumes its output rather than
    re-deriving a rate, so a counter that went backwards (a livekit-server
    restart, or a reboot zeroing ``node_cpu_seconds_total``) arrives here as
    ``None`` and stays ``None``. A second copy of that rule would render a reset
    as an idle node, which is a clean bill of health at exactly the moment
    something restarted.

    ``fraction`` is ``None``, never 0.0, whenever the pair cannot be sourced:
    either side unknown, a non-positive capacity rate (nothing to be a fraction
    of), or an idle rate above capacity (the two do not describe the same
    machine, so the number they imply is not a measurement).
    """
    by_at_ms = {r.at_ms: r.per_second for r in idle}
    out: list[UtilisationPoint] = []
    for point in capacity:
        total = point.per_second
        idle_rate = by_at_ms.get(point.at_ms)
        if total is None or idle_rate is None or total <= 0.0:
            out.append(UtilisationPoint(at_ms=point.at_ms, fraction=None))
            continue
        ratio = idle_rate / total
        if ratio > 1.0 + _UTILISATION_EPSILON:
            out.append(UtilisationPoint(at_ms=point.at_ms, fraction=None))
            continue
        # Clamp the float noise around a fully idle machine to exactly 0.0
        # rather than a tiny negative, which would render as a nonsense
        # utilisation below zero.
        out.append(UtilisationPoint(at_ms=point.at_ms, fraction=max(0.0, 1.0 - ratio)))
    return out


async def read_counter_rate(
    db: AsyncSession,
    *,
    node: str,
    source: str,
    column: str,
    since_ms: int | None = None,
    until_ms: int | None = None,
    limit: int = DEFAULT_READ_LIMIT,
) -> list[CounterRate]:
    """Read one cumulative column and diff it under the rule above.

    ``column`` must name a counter. A gauge is refused rather than diffed: the
    rate of change of ``filefd_allocated`` is not what anyone means by it, and a
    caller that asked for one has a bug. The name is checked against
    :data:`COUNTER_COLUMNS` before it reaches the query, so it can never be a
    caller-supplied SQL fragment.
    """
    if column not in COUNTER_COLUMNS:
        raise ValueError(
            f"{column!r} is not a node_samples counter; counters are "
            f"{sorted(COUNTER_COLUMNS)}. Gauges are read as stored, not diffed."
        )
    rows = await list_samples(
        db,
        node=node,
        source=source,
        since_ms=since_ms,
        until_ms=until_ms,
        limit=limit,
    )
    points: list[tuple[int, float | None]] = []
    for row in rows:
        value = getattr(row, column)
        points.append((row.at_ms, None if value is None else float(value)))
    return counter_rates(points)


async def trim_older_than(
    db: AsyncSession, *, cutoff_ms: int, limit: int = DEFAULT_TRIM_BATCH
) -> int:
    """Delete up to ``limit`` samples older than ``cutoff_ms``; return the count.

    ONE bounded batch per call, not a drain loop, because the caller is the
    scrape worker's tick: at steady state a tick inserts one row per target and
    a 500-row batch clears far more than that, so the table converges without
    any single call holding the write lock long enough to stall the shared
    uvicorn. A backlog (retention shortened, or the worker restarted after a
    long gap) drains over the following ticks instead of in one stop-the-world
    delete.

    ``DELETE ... WHERE id IN (SELECT ... LIMIT)`` rather than ``DELETE ...
    LIMIT``: the latter needs a compile-time option SQLite usually ships
    without, and does not exist in PostgreSQL at all. Same shape as the
    retention worker's passes.
    """
    result = await db.execute(
        text(
            "DELETE FROM node_samples WHERE id IN ("
            "  SELECT id FROM node_samples WHERE at_ms < :cutoff_ms LIMIT :limit)"
        ),
        {"cutoff_ms": cutoff_ms, "limit": limit},
    )
    # Same ignore every repository here carries: execute() is typed Result, and
    # only the CursorResult a DELETE actually returns has rowcount.
    removed = int(result.rowcount or 0)  # type: ignore[attr-defined]
    await db.commit()
    return removed


async def count_samples(db: AsyncSession, *, node: str | None = None) -> int:
    """How many samples are stored (optionally for one node). For tests and ops."""
    if node is None:
        result = await db.execute(text("SELECT COUNT(*) FROM node_samples"))
    else:
        result = await db.execute(
            text("SELECT COUNT(*) FROM node_samples WHERE node = :node"),
            {"node": node},
        )
    return int(result.scalar() or 0)


__all__ = [
    "COUNTER_COLUMNS",
    "DEFAULT_PROJECT",
    "DEFAULT_READ_LIMIT",
    "DEFAULT_TRIM_BATCH",
    "GAUGE_COLUMNS",
    "VALUE_COLUMNS",
    "CounterRate",
    "NodeSampleInput",
    "NodeSampleRow",
    "UtilisationPoint",
    "count_samples",
    "counter_rates",
    "insert_samples",
    "list_samples",
    "read_counter_rate",
    "trim_older_than",
    "utilisation_points",
]
