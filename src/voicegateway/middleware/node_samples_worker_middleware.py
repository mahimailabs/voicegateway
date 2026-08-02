"""Prometheus scrape worker: livekit-server / livekit-sip / node_exporter.

Mirrors ``AgentObservationsWorker``: one background asyncio task, a periodic
tick, exceptions logged and swallowed so the loop survives them. What differs is
that this one talks to the network, which is where all of its hazards live.

**Three properties this worker exists to hold.**

*One process, one port.* The webhook receiver, the load poller, the dashboard
and this share a single uvicorn. So: every target is scraped concurrently rather
than in series (a 10-node tick lasts one timeout, not ten), every request is
under BOTH an httpx timeout and an outer ``asyncio.wait_for`` deadline, the
response body is capped, and the tick sleeps AFTER it finishes so ticks cannot
pile up on a slow target. Nothing here blocks the event loop.

*Retention is not optional.* ~10 nodes at 15 s is ~57k rows/day on the same
SQLite file that absorbs webhook bursts. Every tick ends with a bounded
``trim_older_than``, unconditionally, so the table is self-limiting whatever the
retention config says. The per-project retention pass also has a branch for the
table, but it is opt-in (``retention.enabled``) and discovers its projects from
``requests``/``sessions``, which a node-only deployment never populates -- so it
cannot be the guarantee.

*A gap is recorded, not hidden.* A scrape that fails still writes a row, with
``outcome`` saying why and every value NULL. A series the exposition did not
carry stays NULL and is counted out of ``series_found``. Nothing is stored as 0
unless a target reported 0.

WHERE THESE NAMES CAME FROM: ALL OF THEM MEASURED
-------------------------------------------------
These names were originally taken from
``docs/superpowers/specs/2026-07-29-end-to-end-profiling-scope.md`` (§2 "What is
actually observable"), which was written against the SDKs installed here rather
than against a running binary. A Python SDK version does not pin the
livekit-server or livekit-sip BINARY an operator runs, and inferring a metric
name is how a column ends up storing NULL for the life of a deployment while
nothing at runtime says so. Three names taken that way were wrong.

Every wired entry has since been read off a running binary. **Nothing in
:data:`SERIES` is inferred.** The provenance note is kept, and a count check in
``tests/middleware/test_histogram_misuse.py`` enforces it, because the way this
map degrades is one plausible entry added from documentation.

MEASURED against livekit-sip 1.10.1 and node_exporter, with a real inbound call
placed so every counter was populated: all 22 ``livekit-sip`` entries and all 11
``node-exporter`` entries. Every one resolves, asserted per entry against a
captured exposition and again in
``tests/integration/test_live_node_scrape.py`` against the running exporters.

The last two ``node-exporter`` entries are its own ``process_open_fds`` /
``process_max_fds``, read off a live exporter as 8 and 524287. They were left
unwired for a while on the reasoning that an exporter's own handles say nothing
about a service under test, which is true. What made that untenable is that the
descriptor headroom gate asks every scraped source for the pair regardless, so
omitting them did not remove the question, it only guaranteed the answer was
UNKNOWN: seven of one real seven-step run's nine UNKNOWN rows were this exporter
being asked a question nothing had been wired to answer. Wiring them resolves
that without suppressing a gate for a metric the source could have produced.
Note the same 524287 rlimit livekit-sip and livekit-server report on the host,
which is the cross-check that this is a per-process ceiling.

MEASURED against livekit-server 1.10.1, by authenticated scrape of its metrics
port: all 11 ``livekit-server`` entries. The four ``livekit_*`` families and the
two Go runtime series came first; the five ``process_*`` entries were the last to
be confirmed, because that port is commonly behind basic auth and an
unauthenticated request gets a 401 rather than an exposition. Observed:
``process_open_fds`` 20, ``process_max_fds`` 524287,
``process_start_time_seconds`` 1.78553687934e+09, ``process_cpu_seconds_total``
111.39, ``process_resident_memory_bytes`` 7.3125888e+07. That rlimit is the same
524287 livekit-sip reports on the same host, which is the cross-check that these
are the per-process ceiling rather than anything host-wide.

DELIBERATELY UNWIRED, so their columns store NULL rather than a wrong name:
``livekit_session_start_time_ms_*``, ``livekit_track_published_total``,
``livekit_track_subscribed_total`` and ``psrpc_stream_count``. These are the
names that could not be found on a live server under any spelling. An unwired
column is honest; a guessed one is invisible.

node_exporter names come from its filefd / loadavg / cpu / meminfo / sockstat /
netstat collectors and have been stable since node_exporter 0.18 (current 1.9).

A metric name can still be renamed by a release, which is why an unmatched
series stores NULL and why every row records ``series_found``: an operator on a
release that renamed something sees the count fall on the row instead of an
unexplainable empty chart, and fixing it is one entry in :data:`SERIES`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import urllib.parse
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

import httpx

from voicegateway.middleware.base_middleware import AsyncWorker
from voicegateway.middleware.prometheus_exposition import (
    BUCKET_SUFFIX,
    parse_exposition,
    sum_series,
)
from voicegateway.repository import node_samples_repository as node_samples

if TYPE_CHECKING:
    from voicegateway.services.storage_service import StorageService

logger = logging.getLogger(__name__)


# Prometheus' own default scrape interval, and the resolution the ~57k rows/day
# figure in the scope assumes.
_DEFAULT_POLL_INTERVAL_SECONDS: Final[float] = 15.0
# Hard per-target deadline. Deliberately well under the poll interval so a tick
# cannot outlast its own period even when every target is dead.
_DEFAULT_SCRAPE_TIMEOUT_SECONDS: Final[float] = 5.0
# Response cap. node_exporter is ~100 KB; a livekit-server with a wide label
# cardinality is larger but nowhere near this. A target that streams forever is
# stopped by this and by the deadline above.
_DEFAULT_MAX_RESPONSE_BYTES: Final[int] = 4 * 1024 * 1024
# How long a raw sample lives. Seven days is the observation window the scope
# commits to (§7: "the client's 7-day requirement is an observation window, met
# by the scrape worker"), and at ~57k rows/day it caps the table around 400k
# rows instead of the 5.2M that the 90-day project default would allow.
_DEFAULT_MAX_AGE_SECONDS: Final[float] = 7 * 24 * 3600.0

SOURCE_LIVEKIT_SERVER: Final[str] = "livekit-server"
SOURCE_LIVEKIT_SIP: Final[str] = "livekit-sip"
SOURCE_NODE_EXPORTER: Final[str] = "node-exporter"
SOURCES: Final[tuple[str, ...]] = (
    SOURCE_LIVEKIT_SERVER,
    SOURCE_LIVEKIT_SIP,
    SOURCE_NODE_EXPORTER,
)

# Scrape outcomes, stored verbatim in ``node_samples.outcome``.
OUTCOME_OK: Final[str] = "ok"
OUTCOME_TIMEOUT: Final[str] = "timeout"
OUTCOME_UNREACHABLE: Final[str] = "unreachable"
OUTCOME_HTTP_ERROR: Final[str] = "http_error"
OUTCOME_TOO_LARGE: Final[str] = "too_large"
OUTCOME_UNPARSEABLE: Final[str] = "unparseable"

# Comma-separated ``source:name=url`` entries, e.g.
#   livekit-server:sfu-1=http://10.0.0.4:6789/metrics,
#   node-exporter:sfu-1=http://10.0.0.4:9100/metrics
# The same ``name`` on two sources is the point: it is what puts an SFU's own
# counters and its host's file descriptors on one time axis.
TARGETS_ENV_VAR: Final[str] = "VOICEGW_NODE_SCRAPE_TARGETS"


@dataclass(frozen=True)
class ScrapeTarget:
    """One exposition endpoint, and the node name its samples are filed under.

    ``url`` NEVER carries userinfo. A metrics endpoint behind basic auth is
    configured as ``http://user:secret@host/metrics``, and httpx would happily
    authenticate from that: it also logs the request line at INFO, so the
    password would be written to the log on every tick, four times a minute, for
    as long as the process runs. :func:`targets_from_env` splits the credential
    off into :attr:`auth` so the URL that gets logged, retried and reported is
    the URL without it.

    ``auth`` is ``repr=False`` because a dataclass repr is the other way a
    secret escapes: any log line, exception or debugger that renders a target
    would print it.
    """

    node: str
    url: str
    source: str
    project: str = node_samples.DEFAULT_PROJECT
    auth: tuple[str, str] | None = field(default=None, repr=False)


@dataclass(frozen=True)
class _Series:
    """One exposition series and the ``node_samples`` column it lands in."""

    metric: str
    column: str
    # Label constraint, used ONLY where the label value is part of the series'
    # definition (``mode="idle"``). Everything else sums across labels, so a
    # renamed label value cannot silently empty a column.
    where: Mapping[str, str] | None = None


# The metric map. See the module docstring for where these names came from and
# why an unmatched one is NULL rather than 0.
SERIES: Final[dict[str, tuple[_Series, ...]]] = {
    SOURCE_LIVEKIT_SERVER: (
        _Series("livekit_room_total", "rooms"),
        _Series("livekit_participant_total", "participants"),
        # Summed across direction/transmission/country: this schema stores a
        # node total, and the label VALUES ("in" vs "incoming") are exactly the
        # kind of detail that drifts between releases.
        _Series("livekit_packet_total", "packets_total"),
        _Series("livekit_packet_bytes", "packet_bytes_total"),
        # Go runtime and process collectors, from the standard
        # prometheus/client_golang set both binaries register by default. The
        # heap and goroutine pair is the return-to-baseline signal, NOT RSS: Go
        # hands freed heap back to the OS lazily, so a drained process can hold
        # its resident size long after the heap emptied.
        #
        # process_max_fds is the PER-PROCESS rlimit and is the ceiling a service
        # actually hits, unlike the host fs.file-max which is commonly
        # unbounded. process_start_time_seconds is what makes a mid-run restart
        # visible rather than an unexplained discontinuity in every counter rate
        # across it.
        _Series("go_memstats_heap_inuse_bytes", "heap_inuse_bytes"),
        _Series("go_goroutines", "go_goroutines"),
        _Series("process_open_fds", "process_open_fds"),
        _Series("process_max_fds", "process_max_fds"),
        _Series("process_start_time_seconds", "process_start_time_seconds"),
        _Series("process_cpu_seconds_total", "process_cpu_seconds_total"),
        _Series("process_resident_memory_bytes", "process_resident_memory_bytes"),
        # NOT here: livekit_node_cpu_load, which exists on livekit-sip only and
        # appears in none of the livekit_* families the server exports.
        #
        # ALSO NOT here, and their columns therefore store NULL:
        # livekit_session_start_time_ms_sum/_count, livekit_track_published_total,
        # livekit_track_subscribed_total and psrpc_stream_count. They are
        # server-side names that could not be read off a live server, and a
        # guessed name stores NULL for the life of a deployment while
        # series_found still reads high because its siblings matched. An
        # unwired column is honest; a wrong one is invisible.
    ),
    SOURCE_LIVEKIT_SIP: (
        # Fleet aggregates only. livekit-sip is blind to anything per-call, so
        # none of these may ever be attributed to a `calls` row.
        _Series("livekit_sip_calls_active", "sip_calls_active"),
        _Series("livekit_sip_invite_requests_raw", "sip_invite_requests_raw_total"),
        _Series("livekit_sip_invite_requests", "sip_invite_requests_total"),
        _Series("livekit_sip_invite_accepted", "sip_invite_accepted_total"),
        # Summed across the `status` label: the per-status split is a different
        # (and wider) table than this one.
        _Series("livekit_sip_calls_terminated", "sip_calls_terminated_total"),
        # Go runtime and process collectors, from the standard
        # prometheus/client_golang set both binaries register by default. The
        # heap and goroutine pair is the return-to-baseline signal, NOT RSS: Go
        # hands freed heap back to the OS lazily, so a drained process can hold
        # its resident size long after the heap emptied.
        #
        # process_max_fds is the PER-PROCESS rlimit and is the ceiling a service
        # actually hits, unlike the host fs.file-max which is commonly
        # unbounded. process_start_time_seconds is what makes a mid-run restart
        # visible rather than an unexplained discontinuity in every counter rate
        # across it.
        _Series("go_memstats_heap_inuse_bytes", "heap_inuse_bytes"),
        _Series("go_goroutines", "go_goroutines"),
        _Series("process_open_fds", "process_open_fds"),
        _Series("process_max_fds", "process_max_fds"),
        _Series("process_start_time_seconds", "process_start_time_seconds"),
        _Series("process_cpu_seconds_total", "process_cpu_seconds_total"),
        _Series("process_resident_memory_bytes", "process_resident_memory_bytes"),
        # ---- answer latency ------------------------------------------------
        # The engagement's headline risk: livekit-sip withholds 200 OK until it
        # has subscribed to an audio track, so this histogram IS caller-visible
        # answer latency.
        #
        # MEASURED, not reasoned. Its HELP string on 1.10.1 reads "SIP room join
        # duration (from INVITE to mixed room audio)", which names both
        # boundaries, and the arithmetic on a real call agrees: session_sec_sum
        # 8.670751059 minus call_sec_sum 8.613975456 is 0.056775603 against a
        # join_sec_sum of 0.056595646, a disagreement of 0.18 ms. Join is
        # therefore the pre-200-OK segment of the session, which is what makes
        # it the answer latency rather than a number that correlates with it.
        #
        # Stored as _sum and _count; the buckets are cumulative and summing them
        # counts one observation once per bucket.
        _Series("livekit_sip_dur_join_sec_sum", "sip_join_sec_sum"),
        _Series("livekit_sip_dur_join_sec_count", "sip_join_sec_count"),
        # Two explicit buckets, each selected by an le naming ONE bound, so a
        # proportion under it is answerable without inventing a percentile.
        _Series(
            "livekit_sip_dur_join_sec_bucket", "sip_join_le1_count", where={"le": "1"}
        ),
        _Series(
            "livekit_sip_dur_join_sec_bucket", "sip_join_le5_count", where={"le": "5"}
        ),
        _Series("livekit_sip_dur_check_sec_sum", "sip_check_sec_sum"),
        _Series("livekit_sip_dur_check_sec_count", "sip_check_sec_count"),
        # ---- admission -----------------------------------------------------
        # "Whether node can accept new requests". Flips to 0 when the node stops
        # taking INVITEs, which serves both the health clause and the drain test.
        _Series("livekit_sip_available", "sip_available"),
        # livekit-sip ONLY. Absent from every livekit_* family on the server.
        _Series("livekit_node_cpu_load", "sip_node_cpu_load"),
        # ---- media ---------------------------------------------------------
        # SPLIT BY DIRECTION, never summed: a call that sent 337 and received
        # 330 sums to 667 and one-way audio disappears into that one number.
        # Not also filtered on payload: the send leg carries the literal string
        # "audio" rather than a codec name, so a payload filter would empty it.
        _Series(
            "livekit_sip_packets_rtp", "sip_rtp_packets_recv", where={"op": "recv"}
        ),
        _Series(
            "livekit_sip_packets_rtp", "sip_rtp_packets_send", where={"op": "send"}
        ),
    ),
    SOURCE_NODE_EXPORTER: (
        # The M4 headline pair.
        _Series("node_filefd_allocated", "filefd_allocated"),
        _Series("node_filefd_maximum", "filefd_maximum"),
        _Series("node_load1", "load1"),
        # Summed over every cpu and mode; the idle subset is the one place a
        # label value is part of the definition rather than a split.
        _Series("node_cpu_seconds_total", "cpu_seconds_total"),
        _Series(
            "node_cpu_seconds_total", "cpu_idle_seconds_total", where={"mode": "idle"}
        ),
        _Series("node_memory_MemAvailable_bytes", "memory_available_bytes"),
        _Series("node_memory_MemTotal_bytes", "memory_total_bytes"),
        # ---- port headroom -------------------------------------------------
        # UDP sockets in use, and ports that could not be allocated. The second
        # is the one that matters: a rising rate means the box ran out of ports,
        # which at 500 concurrent is a likelier wall than CPU.
        _Series("node_sockstat_UDP_inuse", "sockstat_udp_inuse"),
        _Series("node_netstat_Udp_NoPorts", "udp_no_ports_total"),
        # ---- this exporter's own process ------------------------------------
        # Wired because the descriptor headroom gate asks every scraped source
        # for this pair, and a gate that is asked but not fed produced seven of
        # one real run's nine UNKNOWNs. The two coherent positions are gate and
        # scrape, or neither; the state before this was the middle one.
        #
        # READ WHAT THIS IS. These are node_exporter's OWN file handles, not any
        # service's. The subject on the resulting gate is
        # ``<node>/node-exporter/file_descriptors`` and it means exactly that.
        # It is not evidence about livekit-sip's headroom and must never be read
        # as such: the services under test report their own pair, and those are
        # the rows that answer the acceptance criterion.
        _Series("process_open_fds", "process_open_fds"),
        _Series("process_max_fds", "process_max_fds"),
    ),
}


# The host file-descriptor maximum a kernel reports when nothing constrains it.
# VERIFIED LIVE: node_filefd_maximum reads 9.223372036854776e+18, and
# int(float(...)) of that is 9223372036854775808, exactly ONE past the 64-bit
# ceiling. That is a float64 round-trip artifact of a number that is already
# 2**63 - 1, not a machine with more descriptors than a signed 64-bit integer
# can count. So the column is left NULL and the fact is recorded separately.
#
# The threshold is short of 2**63 rather than equal to it because the round trip
# is lossy in both directions: any value this close to the ceiling is the
# kernel's "no limit", and the last 1024 counts are not a distinction anything
# can act on.
FILEFD_UNBOUNDED_THRESHOLD: Final[float] = float(2**63 - 1024)
_FILEFD_MAXIMUM_COLUMN: Final[str] = "filefd_maximum"
_FILEFD_UNBOUNDED_COLUMN: Final[str] = "filefd_maximum_unbounded"


def _mark_unbounded_filefd(values: dict[str, float | None]) -> None:
    """Record whether the host descriptor ceiling is reachable at all.

    THREE states, and keeping them apart is the whole point. 1 says the maximum
    parsed as effectively unbounded, so headroom on it cannot run out. 0 says it
    parsed as a real bound. Leaving the key out entirely stores NULL, which says
    nobody measured it.

    Without this, an unbounded ceiling and an unscraped one are the same empty
    column, and "this limit cannot be hit" reads identically to "we never
    looked". They support opposite conclusions about a fleet.
    """
    maximum = values.get(_FILEFD_MAXIMUM_COLUMN)
    if maximum is None:
        # Absent or unparseable. NOT 0: an absent series says nothing about
        # whether the limit is reachable.
        return
    values[_FILEFD_UNBOUNDED_COLUMN] = (
        1.0 if maximum >= FILEFD_UNBOUNDED_THRESHOLD else 0.0
    )


#: Sources that report facts about the HOST they run on: node-wide CPU, memory,
#: load, host file-descriptor totals, socket counts.
#:
#: VERIFIED BY SCRAPE, not by reading this map. node_exporter publishes
#: node_cpu_seconds_total and node_memory_Mem*_bytes; livekit-sip publishes
#: NEITHER, and no amount of load will make it start.
HOST_EXPORTERS: frozenset[str] = frozenset({SOURCE_NODE_EXPORTER})

#: Sources that are a SERVICE reporting on itself. They publish the Go runtime
#: and process collectors for their own process and nothing about the box.
SERVICE_EXPORTERS: frozenset[str] = frozenset(
    {SOURCE_LIVEKIT_SERVER, SOURCE_LIVEKIT_SIP}
)


def any_source_publishes(*columns: str) -> bool:
    """Whether ANY declared source is wired to populate every column named.

    The question behind a scope exclusion. A headroom resource computed from
    columns nothing publishes cannot be measured by this system on any node, in
    any run, so reporting it per node per test says the same nothing many times.

    Derived rather than listed, so the exclusion lifts by itself. Wire a source
    for the columns and the resource stops being excluded and goes back to being
    a real per-node gate, with no second place to remember to update.
    """
    return any(
        all(column in columns_for(source) for column in columns) for source in SERIES
    )


def columns_for(source: str) -> frozenset[str]:
    """Every ``node_samples`` column this source is wired to populate."""
    return frozenset(entry.column for entry in SERIES.get(source, ()))


def reports_host_metrics(source: str | None) -> bool | None:
    """Whether ``source`` can report NODE-WIDE facts. None when unknowable.

    The one question that is safe to suppress a gate on, and it is deliberately
    narrow.

    A service exporter cannot report node CPU or node memory. Verified against a
    live livekit-sip, which publishes zero node-wide series: grading it UNKNOWN
    says a measurement failed when none was ever attempted or possible.

    This is NOT the same question as "does :data:`SERIES` wire this column for
    this source", and the difference cost a round. That map encodes which
    subject each source is the AUTHORITY for, not what it is capable of.
    node_exporter publishes process_open_fds for its own process (a live one
    reads 9), and the map omits it deliberately, because node_exporter's own
    file handles say nothing about livekit-sip's headroom. Reading that omission
    as an inability suppresses a gate for a metric the source could have
    produced, which is the signal, not the noise.

    An undeclared source answers None and is never suppressed.
    """
    if source is None:
        return None
    if source in HOST_EXPORTERS:
        return True
    if source in SERVICE_EXPORTERS:
        return False
    return None


def validate_series_map(series_map: Mapping[str, tuple[_Series, ...]]) -> None:
    """Refuse a metric map that cannot produce meaningful numbers.

    Called at import, so a bad entry is a startup failure rather than a column
    that quietly stores the wrong thing for the life of a deployment. Both
    checks exist because both failure modes were observed against a real
    exposition, and neither announces itself at runtime.

    A ``_bucket`` entry without an ``le`` selector would sum cumulative buckets
    and count each observation once per bucket. On a real capture that reads
    11.0 for a histogram whose true count is 1.

    A base histogram name (``livekit_sip_dur_join_sec`` rather than its ``_sum``
    or ``_count``) matches no sample and stores NULL forever, while
    ``series_found`` still reads high because the sibling entries matched. That
    one cannot be caught without an exposition to compare against, so it is
    checked by a test against a captured fixture rather than here.
    """
    problems: list[str] = []
    for source, entries in series_map.items():
        for entry in entries:
            if entry.metric.endswith(BUCKET_SUFFIX) and not (
                entry.where and "le" in entry.where
            ):
                problems.append(
                    f"{source}: {entry.metric!r} -> {entry.column!r} is a "
                    "cumulative bucket with no le selector; summing it counts "
                    "each observation once per bucket"
                )
    if problems:
        raise ValueError(
            "the node_samples metric map has entries that cannot produce a "
            "meaningful number:\n  " + "\n  ".join(problems)
        )


validate_series_map(SERIES)


TargetProvider = Callable[[], Awaitable[Sequence[ScrapeTarget]]]


def targets_from_env(
    environ: Mapping[str, str] | None = None,
) -> list[ScrapeTarget]:
    """Parse :data:`TARGETS_ENV_VAR` into targets, skipping malformed entries.

    A typo drops one target with a warning rather than raising: this is read on
    a background worker's first tick inside a process that is also serving the
    dashboard, and a bad env var must not take that process down.
    """
    raw = (environ if environ is not None else os.environ).get(TARGETS_ENV_VAR, "")
    targets: list[ScrapeTarget] = []
    for entry in raw.split(","):
        item = entry.strip()
        if not item:
            continue
        head, sep, url = item.partition("=")
        source, colon, node = head.partition(":")
        # Redacted before it reaches a log line. A skipped entry is the case
        # where a credential is MOST likely present and least likely to have
        # been parsed out yet, so the warning about it is a leak of its own.
        shown = redact_url(item)
        if not sep or not colon or not url.strip() or not node.strip():
            logger.warning(
                "%s: ignoring %r; expected 'source:name=url'", TARGETS_ENV_VAR, shown
            )
            continue
        if source not in SOURCES:
            logger.warning(
                "%s: ignoring %r; unknown source %r (known: %s)",
                TARGETS_ENV_VAR,
                shown,
                source,
                ", ".join(SOURCES),
            )
            continue
        clean, auth = split_userinfo(url.strip())
        targets.append(
            ScrapeTarget(node=node.strip(), url=clean, source=source, auth=auth)
        )
    return targets


# Anything between "://" and the "@" that ends an authority. Used to redact a
# URL before it is logged, so a credential cannot ride into the log on a path
# that never went through targets_from_env: a directly constructed target, or a
# malformed env entry that is skipped before it is ever parsed.
_USERINFO = re.compile(r"(?<=://)[^@/\s]+@")
_REDACTED = "***@"


def redact_url(text: str) -> str:
    """``text`` with any URL userinfo replaced by ``***``.

    Applied at every point that logs a URL rather than only at the parse site,
    because the parse site is not the only way a target is built and a secret
    written to a log file cannot be unwritten.
    """
    return _USERINFO.sub(_REDACTED, text)


def split_userinfo(url: str) -> tuple[str, tuple[str, str] | None]:
    """``(url_without_userinfo, auth_or_None)``.

    httpx authenticates from userinfo on its own, so leaving it in the URL
    WORKS -- and logs the password at INFO on every request. Splitting it here
    keeps the behaviour and drops the leak.

    The credential is percent-decoded, because that is how it travels in a URL
    and how httpx expects it in an auth tuple: a password containing ``@`` or
    ``/`` is only expressible encoded.
    """
    parts = urllib.parse.urlsplit(url)
    userinfo, sep, host = parts.netloc.rpartition("@")
    if not sep:
        return url, None
    username, _, password = userinfo.partition(":")
    auth = (urllib.parse.unquote(username), urllib.parse.unquote(password))
    return urllib.parse.urlunsplit(parts._replace(netloc=host)), auth


async def _default_target_provider() -> list[ScrapeTarget]:
    """Targets from the environment. Empty (a no-op worker) when unset."""
    targets = targets_from_env()
    if not targets:
        logger.debug(
            "NodeSamplesWorker: %s is unset or empty; nothing to scrape",
            TARGETS_ENV_VAR,
        )
    return targets


class NodeSamplesWorker(AsyncWorker):
    """Background worker that scrapes Prometheus targets into ``node_samples``."""

    def __init__(
        self,
        storage: StorageService,
        target_provider: TargetProvider | None = None,
        poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
        scrape_timeout_seconds: float = _DEFAULT_SCRAPE_TIMEOUT_SECONDS,
        max_age_seconds: float = _DEFAULT_MAX_AGE_SECONDS,
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
        trim_batch: int = node_samples.DEFAULT_TRIM_BATCH,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError(
                f"poll_interval_seconds must be > 0, got {poll_interval_seconds}"
            )
        if scrape_timeout_seconds <= 0:
            raise ValueError(
                f"scrape_timeout_seconds must be > 0, got {scrape_timeout_seconds}"
            )
        if max_age_seconds <= 0:
            raise ValueError(f"max_age_seconds must be > 0, got {max_age_seconds}")
        if max_response_bytes < 1:
            raise ValueError(
                f"max_response_bytes must be >= 1, got {max_response_bytes}"
            )
        if trim_batch < 1:
            raise ValueError(f"trim_batch must be >= 1, got {trim_batch}")
        self._storage = storage
        self._provider: TargetProvider = target_provider or _default_target_provider
        self._poll_interval = poll_interval_seconds
        self._scrape_timeout = scrape_timeout_seconds
        self._max_age_seconds = max_age_seconds
        self._max_response_bytes = max_response_bytes
        self._trim_batch = trim_batch
        self._transport = transport
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Spawn the scrape loop. Idempotent."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="node-samples-worker")

    async def stop(self) -> None:
        """Cancel the loop and clear state."""
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def tick_now(self) -> int:
        """Run one scrape pass synchronously. Returns the row count written."""
        return await self._tick()

    # ---- internals -------------------------------------------------------

    async def _loop(self) -> None:
        try:
            while True:
                try:
                    await self._tick()
                except Exception:
                    logger.exception("NodeSamplesWorker tick raised; continuing")
                # Sleep AFTER the tick, never on a schedule: a slow pass delays
                # the next one instead of stacking a second scrape on top of it.
                await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:
            raise

    async def _tick(self) -> int:
        targets = await self._provider()
        if not targets:
            return 0

        # One timestamp for the whole pass, so every target in a tick lines up on
        # the time axis a (node, time) correlation reads.
        at_ms = int(time.time() * 1000)

        timeout = httpx.Timeout(self._scrape_timeout)
        async with httpx.AsyncClient(
            timeout=timeout, transport=self._transport, follow_redirects=False
        ) as client:
            # asyncio.gather wraps each coroutine in its own Task, and a Task
            # runs its coroutine in a COPY of the current context. That is the
            # isolation the ContextVar hazard needs (N jobs sharing one process
            # otherwise merge into a single `session_id`); it is also why this
            # worker cannot leak a target's state into another's. Nothing here
            # sets a ContextVar or calls attach() in the first place -- a node
            # sample belongs to a box, not to a session.
            samples = await asyncio.gather(
                *(self._scrape(client, target, at_ms) for target in targets)
            )

        await self._storage._ensure_initialized()
        async with self._storage._conn.session() as db:
            written = await node_samples.insert_samples(db, samples)
            # Unconditional, every tick: this is what makes the table bounded
            # regardless of whether per-project retention is enabled at all.
            cutoff_ms = at_ms - int(self._max_age_seconds * 1000)
            trimmed = await node_samples.trim_older_than(
                db, cutoff_ms=cutoff_ms, limit=self._trim_batch
            )
        failures = [s for s in samples if s.outcome != OUTCOME_OK]
        if failures:
            logger.warning(
                "NodeSamplesWorker: %d of %d target(s) failed: %s",
                len(failures),
                len(samples),
                ", ".join(f"{s.node}/{s.source}={s.outcome}" for s in failures),
            )
        logger.debug(
            "NodeSamplesWorker: wrote %d sample(s), trimmed %d row(s) older than %d",
            written,
            trimmed,
            cutoff_ms,
        )
        return written

    async def _scrape(
        self, client: httpx.AsyncClient, target: ScrapeTarget, at_ms: int
    ) -> node_samples.NodeSampleInput:
        """Scrape one target. Never raises: a failure becomes a row that says so."""
        try:
            body, outcome = await asyncio.wait_for(
                self._fetch(client, target.url, target.auth),
                timeout=self._scrape_timeout,
            )
        except (TimeoutError, httpx.TimeoutException):
            # The outer wait_for is not redundant with httpx's timeouts: httpx
            # applies a READ timeout per read operation, so a target dribbling
            # one byte every second would never trip it and would hold the tick
            # open forever. This is the hard wall.
            outcome, body = OUTCOME_TIMEOUT, None
        except httpx.HTTPError:
            # Connect refused, DNS failure, TLS failure, protocol error.
            outcome, body = OUTCOME_UNREACHABLE, None
        except Exception:
            # An unexpected failure must not take down the whole pass (gather
            # would propagate it and lose every other target's sample).
            logger.exception(
                "NodeSamplesWorker: unexpected error scraping %s (%s)",
                redact_url(target.url),
                target.node,
            )
            outcome, body = OUTCOME_UNREACHABLE, None

        if body is None:
            return node_samples.NodeSampleInput(
                node=target.node,
                source=target.source,
                at_ms=at_ms,
                outcome=outcome,
                project=target.project,
                # NULL, not 0: no exposition was read, so nothing is known about
                # how many series it would have carried.
                series_found=None,
            )

        samples = parse_exposition(body)
        if not samples:
            return node_samples.NodeSampleInput(
                node=target.node,
                source=target.source,
                at_ms=at_ms,
                outcome=OUTCOME_UNPARSEABLE,
                project=target.project,
                series_found=None,
            )

        values: dict[str, float | None] = {}
        found = 0
        for series in SERIES.get(target.source, ()):
            value = sum_series(samples, series.metric, where=series.where)
            # Absent stays absent: the column is left out entirely, which stores
            # NULL. A 0.0 here would be indistinguishable from a real zero
            # reading and would draw a flat line an operator reads as healthy.
            if value is None:
                continue
            values[series.column] = value
            found += 1
        _mark_unbounded_filefd(values)
        return node_samples.NodeSampleInput(
            node=target.node,
            source=target.source,
            at_ms=at_ms,
            outcome=outcome,
            project=target.project,
            # A count of what MATCHED, which is not yet a count of what stored:
            # a value the repository refuses at coercion is dropped after this
            # point. insert_samples recomputes from what actually landed, and
            # this is the input to that.
            series_found=found,
            values=values,
        )

    async def _fetch(
        self,
        client: httpx.AsyncClient,
        url: str,
        auth: tuple[str, str] | None = None,
    ) -> tuple[str | None, str]:
        """GET one exposition body, capped. ``(body_or_None, outcome)``.

        Streamed rather than buffered so the byte cap is enforced while the
        response arrives: a misconfigured target pointed at a log endpoint must
        not be able to allocate its way through this process's memory.

        ``auth`` becomes an Authorization header rather than part of the URL,
        which is the difference between a credential httpx sends and one it also
        logs. ``None`` is httpx's "no auth" and is what an unauthenticated
        target passes.
        """
        async with client.stream("GET", url, auth=auth) as response:
            if response.status_code != 200:
                # Body deliberately unread: an error page is not an exposition.
                return None, OUTCOME_HTTP_ERROR
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > self._max_response_bytes:
                    logger.warning(
                        "NodeSamplesWorker: %s exceeded %d bytes; dropping the scrape",
                        redact_url(url),
                        self._max_response_bytes,
                    )
                    return None, OUTCOME_TOO_LARGE
                chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", errors="replace"), OUTCOME_OK


__all__ = [
    "OUTCOME_HTTP_ERROR",
    "OUTCOME_OK",
    "OUTCOME_TIMEOUT",
    "OUTCOME_TOO_LARGE",
    "OUTCOME_UNPARSEABLE",
    "OUTCOME_UNREACHABLE",
    "SERIES",
    "SOURCES",
    "SOURCE_LIVEKIT_SERVER",
    "SOURCE_LIVEKIT_SIP",
    "SOURCE_NODE_EXPORTER",
    "HOST_EXPORTERS",
    "SERVICE_EXPORTERS",
    "TARGETS_ENV_VAR",
    "any_source_publishes",
    "columns_for",
    "reports_host_metrics",
    "NodeSamplesWorker",
    "ScrapeTarget",
    "TargetProvider",
    "redact_url",
    "split_userinfo",
    "targets_from_env",
]
