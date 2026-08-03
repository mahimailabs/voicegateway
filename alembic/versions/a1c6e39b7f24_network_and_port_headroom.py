"""node_samples: network allowance counters and media-port headroom

Revision ID: a1c6e39b7f24
Revises: f2b9d47c1a86
Create Date: 2026-08-03 09:20:00.000000

Two acceptance criteria reported UNKNOWN in every report, and not because the
fleet hosts were quiet. The hosts publish all nine of these series; the
collector's series map simply did not know the names, so the scrape matched
nothing, stored nothing, and the gate had no column to read. An UNKNOWN that
comes from a missing map entry is indistinguishable in the report from an
UNKNOWN that comes from an unreachable node, which is the worse half of the
problem: the report looked like it was telling the truth.

NETWORK HEADROOM. Cloud hosts shape traffic rather than dropping it silently,
and the five ethtool allowance counters are where that shaping is admitted. A
throttled instance presents to a caller as jitter and loss that no application
metric explains, so without these columns the criterion could only ever be
argued, never measured. ``network_receive_bytes_total`` and
``network_transmit_bytes_total`` are the throughput the allowances are a ceiling
on, split by direction and never summed: a total hides one-way audio, the same
reason the sip_rtp_packets pair is stored split.

The allowance counters are CUMULATIVE SINCE DRIVER RESET, so only a DELTA over
the test window is attributable to the run. This is not a theoretical caution:
one real SIP node reads 9613 on bw_in from before the engagement started while
its twin, same instance type and same config, reads 0. Gating on the absolute
would fail the first node for throttling it never suffered during the test and
pass the second for the same behaviour. BigInteger on all five, because they are
unbounded counters and an INT4 that overflows is a 500 on PostgreSQL and a
silent wrong number on SQLite.

RTP PORT HEADROOM. ``media_ports_in_use`` counts UDP sockets bound inside the
configured media range, which is narrower and more useful than
``sockstat_udp_inuse``: the latter counts every UDP socket on the host,
signalling and DNS included, so it cannot be read as a fraction of the range.

``media_ports_total`` is a DECLARED CONFIG VALUE rather than a measurement: it
is the size of the configured rtp_port range (10001 on the fleet), published by
the host because only the host knows what it was started with. It is stored per
sample so a ratio is taken against the range in force at that instant rather
than against whatever the config file says at read time. A node that publishes
in_use without total must therefore report UNKNOWN headroom. Dividing by a
guessed range size would mint a percentage nobody measured, and a saturation
chart is exactly where an invented denominator does the most damage.

None of the nine is derived. All are scraped from an exposition, so all nine
count toward ``series_found`` and none belongs in DERIVED_COLUMNS.

Chained off the single head f2b9d47c1a86. Every column is nullable, and NULL
means "not measured", never zero.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c6e39b7f24"
down_revision: str | Sequence[str] | None = "f2b9d47c1a86"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "node_samples"

# (column, type). BigInteger for the cumulative counters and the byte totals;
# a plain Integer for the two port gauges, which are bounded by the size of a
# port range.
_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("media_ports_total", sa.Integer()),
    ("media_ports_in_use", sa.Integer()),
    ("ethtool_bw_in_allowance_exceeded", sa.BigInteger()),
    ("ethtool_bw_out_allowance_exceeded", sa.BigInteger()),
    ("ethtool_pps_allowance_exceeded", sa.BigInteger()),
    ("ethtool_conntrack_allowance_exceeded", sa.BigInteger()),
    ("ethtool_linklocal_allowance_exceeded", sa.BigInteger()),
    ("network_receive_bytes_total", sa.BigInteger()),
    ("network_transmit_bytes_total", sa.BigInteger()),
)


def upgrade() -> None:
    for name, type_ in _COLUMNS:
        op.add_column(_TABLE, sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(_COLUMNS):
        op.drop_column(_TABLE, name)
