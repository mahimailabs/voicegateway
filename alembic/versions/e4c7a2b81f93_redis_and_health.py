"""node_samples: Redis state and health-endpoint probe results

Revision ID: e4c7a2b81f93
Revises: d1f4b8c93a27
Create Date: 2026-08-02 15:40:00.000000

Two acceptance criteria had no coverage at all. "No sustained Redis or
health-check failures" produced no gate on a real run: not a pass, not a fail,
not even an UNKNOWN, because nothing scraped Redis and nothing probed a health
endpoint. A report that silently omits an agreed criterion is worse than one
that fails it.

ONE revision for both groups, and chained off the single head d1f4b8c93a27.
Two revisions landing in the same branch is how the chain forks, and a forked
chain fails only at ``alembic upgrade head`` on a real deployment.

REDIS names were read off a live oliver006/redis_exporter scraping redis:7,
not taken from documentation. Confirmed present with values: ``redis_up`` 1,
``redis_rejected_connections_total`` 0, ``redis_memory_used_bytes`` 1194336,
``redis_memory_max_bytes`` 0, ``redis_blocked_clients`` 0,
``redis_evicted_keys_total`` 0.

``redis_memory_max_bytes`` reads 0 on an exporter whose Redis has no maxmemory
configured, and 0 there does NOT mean "no memory available", it means "no
limit". Dividing used by it would either divide by zero or read as total
exhaustion, so ``redis_memory_max_unbounded`` records the difference the same
way ``filefd_maximum_unbounded`` already does for the host descriptor ceiling:
1 unbounded, 0 a real limit, NULL unmeasured.

HEALTH is not a Prometheus series and gets no parallel probe subsystem. The
outcome is written by the same sampling loop onto the same time axis as
everything else, so a sustained-failure gate reasons over a window of samples
exactly like every other criterion, at the cost of three columns.

``health_ok`` is 1 for any 2xx, 0 otherwise, and NULL when no health endpoint is
configured. NULL must never be read as 0: "nobody asked" and "it answered badly"
are different facts, and an operator has to be able to tell an unconfigured
probe from a failing service.

``health_status_code`` carries the status when a response arrived at all and is
NULL when none did, which is what separates "returned 503" from "refused the
connection". livekit-sip answers 200 OK, 429 UnderLoad or 503 Unavailable on
its health_port; livekit-server answers 200 or 406 on its main port. Both were
read from their own documentation rather than assumed.

``health_timed_out`` splits the two ways a probe gets no response at all. A
refusal means nothing is listening; a timeout means something is listening and
stuck. Both leave the status code NULL and they are different problems.

Every column is nullable and NULL means "not measured", never zero. Byte and
counter columns are BigInteger because INT4 overflows.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4c7a2b81f93"
down_revision: str | Sequence[str] | None = "d1f4b8c93a27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "node_samples"

# (column, type). BigInteger wherever the value is a byte count or a cumulative
# counter; a plain Integer for the small markers and the HTTP status.
_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("redis_up", sa.Integer()),
    ("redis_rejected_connections_total", sa.BigInteger()),
    ("redis_memory_used_bytes", sa.BigInteger()),
    ("redis_memory_max_bytes", sa.BigInteger()),
    ("redis_memory_max_unbounded", sa.Integer()),
    ("redis_blocked_clients", sa.Integer()),
    ("redis_evicted_keys_total", sa.BigInteger()),
    ("health_ok", sa.Integer()),
    ("health_status_code", sa.Integer()),
    ("health_timed_out", sa.Integer()),
)


def upgrade() -> None:
    for name, type_ in _COLUMNS:
        op.add_column(_TABLE, sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(_COLUMNS):
        op.drop_column(_TABLE, name)
