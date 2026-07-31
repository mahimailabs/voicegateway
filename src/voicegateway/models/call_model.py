"""ORM model for the ``calls`` table (one row per call, inference or not).

Until this table existed, a call only appeared in the schema if it ran
inference: ``requests`` is the atomic unit and the ``sessions`` row is derived
inside ``request_log_repository.log_request`` behind ``if record.session_id:``.
A call that answered and said nothing -- or never answered at all -- had no row
anywhere. Rows here are written by paths that never touch inference (the LiveKit
webhook receiver, a load generator reporting its own attempts), keyed on the
LiveKit room SID, which is the one identifier every layer shares.

Forward-only: a row can only exist from the moment a writer is deployed, and
nothing backfills older traffic (an older session has nothing to join to).

Every millisecond column is ``BigInteger``. The absolute ones hold epoch
milliseconds (~1.8e12 today) and the deltas are derived from them, so a plain
INT4 overflows on PostgreSQL while SQLite silently accepts the value -- the same
trap that hit the worker byte-count columns.
"""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import BigInteger, Index, UniqueConstraint
from sqlmodel import Field, SQLModel


class Call(SQLModel, table=True):
    """One call attempt, correlated across SIP -> SFU -> dispatch -> agent."""

    __tablename__: ClassVar[str] = "calls"
    __table_args__ = (
        # Both unique keys are nullable, and NULLs stay distinct in SQLite and
        # PostgreSQL alike. That is precisely why the repository upserts with
        # select-then-update instead of a native ON CONFLICT (see
        # workers_repository.upsert_heartbeat for the same reasoning).
        UniqueConstraint("room_sid", name="uq_calls_room_sid"),
        UniqueConstraint("attempt_id", name="uq_calls_attempt_id"),
        # Join onto sessions.room_name (best effort, see below).
        Index("idx_calls_room_name", "room_name"),
        # Newest-first listing and time-window reads.
        Index("idx_calls_started_at_ms", "started_at_ms"),
        # Per-project reads and the retention pass.
        Index("idx_calls_project", "project"),
        # Every call of one load run.
        Index("idx_calls_run_id", "run_id"),
    )

    # Opaque id minted by the writer (uuid4 hex). ``sessions.call_id`` and
    # ``call_legs.call_id`` point at this.
    id: str = Field(primary_key=True)

    # The LiveKit room SID (``RM_*``): the TRUE per-instance key. Nullable
    # because a 503 on INVITE never creates a room, and that failure is the most
    # important row in a load test.
    room_sid: str | None = None

    # Best-effort join key. NULL is legitimate (no room was ever created), and a
    # deployment that pins one fixed room name would collapse two concurrent
    # calls into one row if this were the key -- it is not.
    room_name: str | None = None

    # Which writer created/observed the row: 'webhook' | 'loadgen' | 'agent'.
    origin: str

    # Load-generator correlation id, unique per placed attempt, so an attempt
    # that never produced a room is still keyed and still counted.
    attempt_id: str | None = None
    # The load run an attempt belongs to. NULL for organic traffic.
    run_id: str | None = None

    project: str = "default"
    tenant_id: str | None = None
    agent_id: str | None = None
    # 'sip' | 'web' | ... as observed; NULL when not known.
    channel: str | None = None
    # 'inbound' | 'outbound'; NULL when not known.
    direction: str | None = None

    started_at_ms: int | None = Field(default=None, sa_type=BigInteger)
    ended_at_ms: int | None = Field(default=None, sa_type=BigInteger)
    # Derived from the two above by the repository, never supplied by a caller,
    # and left NULL when they are not both known (or disagree on ordering).
    duration_ms: int | None = Field(default=None, sa_type=BigInteger)
    end_reason: str | None = None
    # Derived by counting call_legs, so a redelivered webhook cannot inflate it.
    num_legs: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    # 0/1 (integer flag, as elsewhere in this schema). Load traffic is
    # discriminated by arriving on a dedicated load-test SIP trunk, so the writer
    # derives this from the trunk id. It is never taken from a caller-supplied
    # header, and it exists so synthetic load cannot pollute production
    # percentiles.
    is_probe: int = Field(default=0, sa_column_kwargs={"server_default": "0"})

    # Caller-visible answer latency, and how it was derived: 'sipp_rtd' (true
    # INVITE->200 wall time) > agent self-report > webhook proxy
    # (agent_track_published_at_ms - caller_joined_at_ms). One number, one
    # column, one computation -- which lands in a later node; these two columns
    # are created here so the schema is written once. NULL means not derived:
    # never write 0 for an unmeasured latency.
    answer_latency_ms: int | None = Field(default=None, sa_type=BigInteger)
    answer_latency_source: str | None = None
