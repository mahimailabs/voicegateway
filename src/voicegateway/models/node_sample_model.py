"""ORM model for the ``node_samples`` table (layer 7: the boxes, not the calls).

One row per **scrape**, i.e. per ``(node, source, at_ms)``. A node is the
operator's own name for a target (``sfu-1``), and the same node is usually
scraped twice per tick -- once as ``livekit-server`` and once as
``node-exporter`` -- so the two series line up on one time axis.

**No foreign key to ``calls``, and no ``call_id`` column.** Layer 7 is
correlated by ``(node, time window)`` only. livekit-server's
``livekit_packet_*`` and livekit-sip's
``invite_*`` / ``calls_*`` are NODE counters: attributing one of them to a call
would invent a per-call measurement that does not exist server-side (the same
reason ``call_legs`` has no loss / jitter / MOS column).

**Every value column is nullable, and NULL means "not measured".** A series the
target did not expose, a scrape that timed out, and a release that renamed a
metric all store NULL -- never 0. A 0 in ``packets_total`` would render as a
node carrying no traffic, which is the opposite of "we could not see it".
``outcome`` says why the row has no values, and ``series_found`` says how many
of the series expected for that source were actually present, so a rename shows
up as "0 of 5 matched" rather than as an unexplainable empty chart.

**Counters are stored RAW, exactly as scraped, and diffed at read time**
(``node_samples_repository.counter_rates``). Storing a rate at write time would
bake in the counter-reset bug: a livekit-server restart zeroes every ``_total``,
and the honest reading of a decrease is "unknown", which only the read side can
express as NULL.

Byte and file-descriptor columns are ``BigInteger``. ``packet_bytes_total`` on a
busy SFU passes 2 GiB within hours and ``node_filefd_maximum`` is routinely
near ``2**63-1``; an INT4 accepts neither, silently on SQLite and as a 500 on
PostgreSQL. Same trap as the worker byte-count columns and the ``calls``
millisecond columns.
"""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import BigInteger, Index
from sqlmodel import Field, SQLModel


class NodeSample(SQLModel, table=True):
    """One scrape of one node's Prometheus exposition."""

    __tablename__: ClassVar[str] = "node_samples"
    __table_args__ = (
        # The read path: one node's one series, in time order. Leads with the
        # (node, source) pair the counter diff is computed within -- diffing
        # across two sources, or across two nodes, would subtract unrelated
        # counters.
        Index("idx_node_samples_node_source_at_ms", "node", "source", "at_ms"),
        # Both trims (the scrape worker's unconditional one and the per-project
        # retention pass) delete by age, which is the only scan this table has
        # to survive at ~57k rows/day.
        Index("idx_node_samples_at_ms", "at_ms"),
    )

    id: int | None = Field(default=None, primary_key=True)

    # The operator's name for the target, from the scrape config -- NOT the
    # exporter's ``node_id`` label. livekit-server mints a fresh ``node_id`` per
    # process unless it is pinned, so keying on it would split one host's series
    # in two at every restart: exactly when the counter-reset rule needs the two
    # halves adjacent to notice the reset.
    node: str

    # Which exporter produced the row: 'livekit-server' | 'livekit-sip' |
    # 'node-exporter'. Not a foreign key to anything; it is the second half of
    # the series key.
    source: str

    # Scrape wall clock, epoch milliseconds, stamped once per tick by the worker
    # so every target in one pass shares a timestamp. BigInteger: ~1.8e12 today.
    at_ms: int = Field(sa_type=BigInteger)

    # A sample is host-scoped, not project-scoped: a node serves whatever traffic
    # lands on it. The column exists so the row ages out on the same per-project
    # retention pass as everything else, and it is not part of any payload.
    project: str = Field(default="default")

    # How the scrape ended: 'ok' | 'timeout' | 'unreachable' | 'http_error' |
    # 'too_large' | 'unparseable'. A failed scrape still writes a row -- a gap in
    # the series that says why beats a silent gap that reads as "nothing
    # happened".
    outcome: str

    # How many of the series expected for ``source`` were present in the
    # exposition. NULL when the scrape never produced a body. 0 with
    # ``outcome='ok'`` is the signature of a renamed metric, which is otherwise
    # indistinguishable from an idle node.
    series_found: int | None = None

    # ---- livekit-server ---------------------------------------------------
    # Gauges are stored as scraped; counters are cumulative and are diffed at
    # read time.
    rooms: int | None = None
    participants: int | None = None
    packets_total: int | None = Field(default=None, sa_type=BigInteger)
    # BigInteger is load-bearing here (see the module docstring).
    packet_bytes_total: int | None = Field(default=None, sa_type=BigInteger)

    # ---- livekit-sip ------------------------------------------------------
    # Fleet aggregates only. livekit-sip is blind to anything per-call, so
    # nothing here may be joined to a ``calls`` row.
    sip_calls_active: int | None = None
    sip_invite_requests_raw_total: int | None = Field(default=None, sa_type=BigInteger)
    sip_invite_requests_total: int | None = Field(default=None, sa_type=BigInteger)
    sip_invite_accepted_total: int | None = Field(default=None, sa_type=BigInteger)
    sip_calls_terminated_total: int | None = Field(default=None, sa_type=BigInteger)

    # ---- node_exporter (host) ---------------------------------------------
    # The file-descriptor pair is the M4 headline: a ramp knee correlated with
    # ``filefd_allocated`` reaching ``filefd_maximum`` on one host.
    filefd_allocated: int | None = Field(default=None, sa_type=BigInteger)
    # BigInteger, not Integer: ``fs.file-max`` is frequently ~9.2e18.
    filefd_maximum: int | None = Field(default=None, sa_type=BigInteger)
    load1: float | None = None
    # CPU is two counters, not a percentage: busy fraction over a window is
    # ``1 - d(idle)/d(total)``, which only the read-time diff can compute. A
    # stored percentage would have to pick a window at write time and would go
    # negative across a reboot.
    cpu_seconds_total: float | None = None
    cpu_idle_seconds_total: float | None = None
    memory_available_bytes: int | None = Field(default=None, sa_type=BigInteger)
    memory_total_bytes: int | None = Field(default=None, sa_type=BigInteger)

    # ---- Go runtime (livekit-server and livekit-sip are both Go) -----------
    # The return-to-baseline pair: after a load run is torn down and the reap
    # timeouts have passed, these two should come back near where they started.
    #
    # RSS is deliberately NOT one of them. Go returns freed heap to the OS
    # lazily, so a process that has fully drained can hold its resident size for
    # a long time afterwards. Gating on RSS would report a leak that is not one,
    # and worse, it would do so on every healthy run, which trains a reader to
    # ignore the gate. ``heap_inuse`` is what the runtime actually still holds,
    # and a goroutine count that does not come down is the shape a real leak
    # takes here (a per-call goroutine that never exits).
    #
    # ``heap_inuse_bytes`` rather than ``heap_inuse``: every other byte column in
    # this table carries its unit in the name.
    heap_inuse_bytes: int | None = Field(default=None, sa_type=BigInteger)
    go_goroutines: int | None = None

    # ---- answer latency ---------------------------------------------------
    # The engagement's single biggest risk: livekit-sip withholds 200 OK until
    # it has subscribed to an audio track, so the answering participant's join
    # time IS the caller-visible answer latency. These are the only server-side
    # series that measure it.
    #
    # A histogram is stored as its _sum and _count rather than its buckets,
    # because buckets are cumulative and summing them counts one observation
    # once per bucket. Two explicit bucket counts are kept alongside so a
    # proportion under a bound is answerable without a percentile: le=1 and
    # le=5 are the two the acceptance conversation actually turns on.
    sip_join_sec_sum: float | None = None
    sip_join_sec_count: int | None = Field(default=None, sa_type=BigInteger)
    sip_join_le1_count: int | None = Field(default=None, sa_type=BigInteger)
    sip_join_le5_count: int | None = Field(default=None, sa_type=BigInteger)
    sip_check_sec_sum: float | None = None
    sip_check_sec_count: int | None = Field(default=None, sa_type=BigInteger)
    session_start_time_ms_sum: int | None = Field(default=None, sa_type=BigInteger)
    session_start_time_ms_count: int | None = Field(default=None, sa_type=BigInteger)

    # ---- file descriptors, the limit that actually binds -------------------
    # The PER-PROCESS rlimit, not the host fs.file-max. The host figure is
    # commonly unbounded and unstorable, and the limit a service actually hits
    # is its own LimitNOFILE, which node_exporter structurally cannot see.
    process_open_fds: int | None = Field(default=None, sa_type=BigInteger)
    process_max_fds: int | None = Field(default=None, sa_type=BigInteger)

    # ---- admission and media ----------------------------------------------
    # sip_available is "whether this node can accept new requests" and flips to
    # 0 when it stops taking INVITEs, which is both the health signal and the
    # node-drain test.
    sip_available: int | None = None
    # Present on livekit-sip ONLY. Absent from every livekit_* family on the
    # server, so it is never wired there.
    sip_node_cpu_load: float | None = None
    # Split by direction and never summed. A call that sent 337 and received
    # 330 sums to 667, and one-way audio disappears into that single number.
    sip_rtp_packets_recv: int | None = Field(default=None, sa_type=BigInteger)
    sip_rtp_packets_send: int | None = Field(default=None, sa_type=BigInteger)

    # ---- restart and attribution ------------------------------------------
    # A process that restarted mid-run invalidates every counter rate across
    # the restart. start_time is what makes that visible rather than a mystery
    # discontinuity.
    process_start_time_seconds: float | None = None
    process_cpu_seconds_total: float | None = None
    process_resident_memory_bytes: int | None = Field(default=None, sa_type=BigInteger)

    # ---- port headroom ----------------------------------------------------
    # UDP sockets in use against ports that could not be allocated. The second
    # is the one that matters: a non-zero rate means the box ran out of ports,
    # which at 500 concurrent is a far more likely wall than CPU.
    sockstat_udp_inuse: int | None = Field(default=None, sa_type=BigInteger)
    udp_no_ports_total: int | None = Field(default=None, sa_type=BigInteger)

    # ---- stale state ------------------------------------------------------
    # Read for what does NOT come down after teardown. Named _total by the
    # exporter but behaving as gauges: they are current counts, not cumulative,
    # so they are never diffed.
    track_published_total: int | None = Field(default=None, sa_type=BigInteger)
    track_subscribed_total: int | None = Field(default=None, sa_type=BigInteger)
    psrpc_stream_count: int | None = Field(default=None, sa_type=BigInteger)

    # ---- unbounded marker -------------------------------------------------
    # THREE states, and the distinction between them is the entire point.
    # 1 means the host file-descriptor maximum parsed as effectively unbounded,
    # so headroom on it cannot run out. 0 means it parsed as a real bound. NULL
    # means nobody measured it. "Cannot run out" and "nobody looked" are
    # different claims and this table's doctrine forbids collapsing them.
    #
    # Integer rather than Boolean so it travels the same coercion path as every
    # other scraped column instead of needing a parallel one. It is derived
    # from node_filefd_maximum rather than scraped directly, but it is still an
    # observation about one scrape, which is what a value column is.
    filefd_maximum_unbounded: int | None = None
