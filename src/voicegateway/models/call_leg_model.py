"""ORM model for the ``call_legs`` table (one row per participant of a call).

A leg is a participant's slice of a call: when it joined, when it left, why, and
(for a SIP participant) the ``sip.*`` attributes LiveKit puts on it. Written from
participant/track webhooks, which are neither ordered nor exactly-once, so the
repository upserts on ``(call_id, participant_sid)``.

Millisecond columns are ``BigInteger`` for the same reason as ``calls``: they
hold epoch milliseconds, which overflow a PostgreSQL INT4.

Deliberately absent: per-leg RTP loss / jitter / MOS. None of it is observable
server-side (``sfu.py`` hardcodes ``loss_pct = 0.0``), and an empty column
invites a UI that renders 0.0 as "no loss".

Two of the timestamps here are the inputs to ``calls.answer_latency_ms`` (the
caller leg's ``joined_at_ms`` and the agent leg's ``first_audio_track_at_ms``),
so each carries a ``*_source`` column naming the writer whose value is stored.
Without it the derived number could not say whether it was built from
whole-second webhook timestamps or from an agent's own millisecond clock, which
is the difference between the two weaker rungs of the precedence rule.
"""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import BigInteger, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


class CallLeg(SQLModel, table=True):
    """One participant's timeline within a call."""

    __tablename__: ClassVar[str] = "call_legs"
    __table_args__ = (
        # The upsert key. Its index leads with ``call_id``, so it also serves
        # every "legs of this call" lookup; a second index on ``call_id`` alone
        # would only add write cost on the ingest hot path.
        UniqueConstraint(
            "call_id", "participant_sid", name="uq_call_legs_call_participant"
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    # ``calls.id`` of the owning call. Not a declared foreign key: SQLite does
    # not enforce them by default, and an ingest path must not fail because two
    # webhooks arrived out of order.
    call_id: str
    # NOT NULL on purpose: it is half of the unique key, and a NULL in a unique
    # constraint stays distinct on both SQLite and PostgreSQL, so a nullable sid
    # would duplicate the leg on every redelivered webhook.
    participant_sid: str

    identity: str | None = None
    # 'SIP' | 'AGENT' | 'STANDARD' as LiveKit reports it; NULL when not known.
    kind: str | None = None
    region: str | None = None

    joined_at_ms: int | None = Field(default=None, sa_type=BigInteger)
    left_at_ms: int | None = Field(default=None, sa_type=BigInteger)
    disconnect_reason: str | None = None
    # 0/1, or NULL when not observed -- distinct from "observed as not
    # publishing".
    is_publisher: int | None = None
    # JSON object of the participant's ``sip.*`` attributes only (callID,
    # callStatus, phoneNumber, trunkID, ruleID). Nothing else is copied. Text,
    # not VARCHAR: it holds a serialized document of no fixed length.
    attributes_json: str | None = Field(default=None, sa_type=Text)

    # First audio track this leg published. For an agent leg this gates the
    # caller's ring time, because livekit-sip withholds 200 OK until it
    # subscribes to an audio track.
    first_audio_track_at_ms: int | None = Field(default=None, sa_type=BigInteger)
    audio_track_sid: str | None = None
    audio_codec: str | None = None

    # Provenance of the two timestamps above the ``calls.answer_latency_ms``
    # computation subtracts (see ``calls_repository._derive_answer_latency``).
    # 'webhook' | 'agent' | 'loadgen', or NULL when the writer did not say.
    #
    # These exist because the precedence rule needs to tell a webhook timestamp
    # from a self-reported one and the values alone cannot: a webhook's
    # ``created_at`` is whole SECONDS, while an agent or load worker that took
    # part in the call reports its own clock at millisecond precision. Same
    # number, an order of magnitude apart in resolution -- and on a 4 s answer
    # latency a second of truncation is 25% error, so a reader must be told
    # which one it is looking at.
    #
    # Each column describes THE VALUE THAT IS STORED, not the last writer: the
    # merge keeps the earliest timestamp, so if a webhook's truncated value wins
    # over an agent's, the stored value is webhook-precision and the column says
    # so. That is what keeps ``answer_latency_source`` reproducible from the leg
    # rows a reader can see.
    joined_at_source: str | None = None
    first_audio_track_at_source: str | None = None
