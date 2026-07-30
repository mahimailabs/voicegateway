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
