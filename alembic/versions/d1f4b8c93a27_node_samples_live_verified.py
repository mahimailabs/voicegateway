"""node_samples: the columns a live LiveKit deployment proved we need

Revision ID: d1f4b8c93a27
Revises: c5a91e37f2b8
Create Date: 2026-08-01 14:20:00.000000

Every name behind these columns was read off a real livekit-server 1.10.1 plus
livekit-sip plus node_exporter, with an inbound SIP call placed so every counter
was populated. Nothing here is inferred from documentation.

ONE revision for all of them, and for the drop, on purpose. Two revisions
landing in the same branch is how the chain forks, and a forked chain fails only
at ``alembic upgrade head`` on a real deployment.

ANSWER LATENCY is the group that earns the rest. livekit-sip does not send
200 OK until it has subscribed to an audio track, so the answering
participant's join time IS the caller-visible answer latency, and
``livekit_sip_dur_join_sec`` is the only server-side series that measures it.
Stored as ``_sum`` and ``_count`` rather than buckets: buckets are cumulative,
so summing them counts one observation once per bucket, which on the captured
exposition reads 11 against a true count of 1. Two explicit bucket counts sit
alongside so a proportion under a bound is answerable without a percentile.

FILE DESCRIPTORS are stored as the PER-PROCESS rlimit, not the host maximum.
The host ``fs.file-max`` on the observed box is effectively unbounded and does
not fit a 64-bit column, while ``process_max_fds`` is a real ceiling the service
actually hits. ``filefd_maximum_unbounded`` records the difference between "the
limit cannot be reached" and "nobody measured it", which the model's own
doctrine forbids collapsing. It is an integer carrying three states (1
unbounded, 0 bounded, NULL unmeasured) rather than a boolean, so it travels the
same coercion path as every other column instead of needing a parallel one.

``nacks_total`` is DROPPED. ``livekit_nack_total`` does not exist on 1.10.1:
zero occurrences of "nack" under any name in the full 77 KB livekit-server
exposition, read by authenticated scrape. livekit-server is where that series
would have lived, so this is the capture that settles it rather than an absence
noticed somewhere it was never expected.
A column nothing can ever populate is worse than no column, because it is
reachable by a chart that will read NULL forever. No substitute is put in its
place: the obvious candidate is declared a gauge while behaving cumulatively.

Every value column is nullable and NULL means "not measured", never zero.
Millisecond and byte columns are BigInteger because INT4 overflows.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d1f4b8c93a27"
down_revision = "c5a91e37f2b8"
branch_labels = None
depends_on = None


# (name, type) for every column added. Kept as data so the upgrade and the
# downgrade cannot drift from one another.
_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    # Answer latency.
    ("sip_join_sec_sum", sa.Float()),
    ("sip_join_sec_count", sa.BigInteger()),
    ("sip_join_le1_count", sa.BigInteger()),
    ("sip_join_le5_count", sa.BigInteger()),
    ("sip_check_sec_sum", sa.Float()),
    ("sip_check_sec_count", sa.BigInteger()),
    ("session_start_time_ms_sum", sa.BigInteger()),
    ("session_start_time_ms_count", sa.BigInteger()),
    # File descriptors.
    ("process_open_fds", sa.BigInteger()),
    ("process_max_fds", sa.BigInteger()),
    # Admission and media.
    ("sip_available", sa.Integer()),
    ("sip_node_cpu_load", sa.Float()),
    ("sip_rtp_packets_recv", sa.BigInteger()),
    ("sip_rtp_packets_send", sa.BigInteger()),
    # Restart and attribution.
    ("process_start_time_seconds", sa.Float()),
    ("process_cpu_seconds_total", sa.Float()),
    ("process_resident_memory_bytes", sa.BigInteger()),
    # Port headroom.
    ("sockstat_udp_inuse", sa.BigInteger()),
    ("udp_no_ports_total", sa.BigInteger()),
    # Stale state.
    ("track_published_total", sa.BigInteger()),
    ("track_subscribed_total", sa.BigInteger()),
    ("psrpc_stream_count", sa.BigInteger()),
    # Derived marker, not a scraped series.
    ("filefd_maximum_unbounded", sa.Integer()),
)


def upgrade() -> None:
    for name, type_ in _COLUMNS:
        op.add_column("node_samples", sa.Column(name, type_, nullable=True))
    # livekit_nack_total does not exist on livekit-server 1.10.1.
    op.drop_column("node_samples", "nacks_total")


def downgrade() -> None:
    op.add_column(
        "node_samples", sa.Column("nacks_total", sa.BigInteger(), nullable=True)
    )
    for name, _ in reversed(_COLUMNS):
        op.drop_column("node_samples", name)
