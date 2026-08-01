"""Async repo for the ``calls`` + ``call_legs`` tables (the call, not the inference).

Writers here never touch inference: the LiveKit webhook receiver, a load
generator reporting its own attempts, or an agent self-report. That is the point
of the table -- a call that ran no inference used to have no row anywhere.

Three properties this module exists to hold:

* **Create-if-missing from any event.** Webhook delivery is neither ordered nor
  exactly-once, so ``participant_left`` may be the first thing we ever see about
  a call. Every event may create the row.
* **Select-then-update, never a native ON CONFLICT.** ``room_sid`` and
  ``attempt_id`` are both nullable uniques, and a NULL stays distinct on SQLite
  *and* PostgreSQL, so an on-conflict upsert duplicates rows instead of merging
  them. Same reasoning as ``workers_repository.upsert_heartbeat``, where a NULL
  ``tenant_id`` duplicated the operator's row on every heartbeat.
* **Merge, do not clobber.** An event carries a subset of the truth, so ``None``
  means "this event did not say", never "set it back to unknown". Timestamps keep
  the earliest start and the latest end, so out-of-order delivery converges.

This module also owns **the headline number**, ``calls.answer_latency_ms``:
:func:`_derive_answer_latency` is the ONLY place in this product that computes
caller-visible answer latency, and :data:`ANSWER_LATENCY_SOURCES` is the closed
set of provenances stored beside it. See the block comment above that function
for the precedence rule and every case that resolves to NULL instead of a number.

Forward-only: nothing here backfills, repairs, or scans history. In particular
nothing re-derives answer latency for rows written before this code existed --
the legs of an old call were never stamped with a provenance, and inventing one
would be worse than the NULL that is there.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from voicegateway.models.call_leg_model import CallLeg
from voicegateway.models.call_model import Call

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_logger = logging.getLogger(__name__)

DEFAULT_PROJECT = "default"

# Only the ``sip.*`` participant attributes are persisted (callID, callStatus,
# phoneNumber, trunkID, ruleID). Everything else on a participant is application
# state that this table has no business copying.
_SIP_ATTRIBUTE_PREFIX = "sip."

# ``ParticipantInfo.Kind`` names, as LiveKit reports them and as both writers
# store them. The caller is the SIP leg; the agent is the AGENT leg.
_SIP_KIND = "SIP"
_AGENT_KIND = "AGENT"

# The CLOSED set of ``calls.answer_latency_source`` values, strongest first.
# Free text here would make the column unreadable: a reader who cannot tell a
# reported INVITE->200 wall time from a whole-second webhook subtraction cannot
# trust either number.
#
#   'sipp_rtd'      the true INVITE->200 OK wall time, as measured and REPORTED
#                   by the load worker that placed the call. It can only ever
#                   arrive as a report: livekit-api 1.1.0 exposes no
#                   ListSIPCallInfo, so LiveKit never tells us a SIP response.
#   'agent_report'  the track-publish subtraction, from two leg timestamps BOTH
#                   self-reported by a process that took part in the call (an
#                   agent or a load worker) -- one clock, millisecond precision.
#   'webhook_proxy' the same subtraction, from leg timestamps that are NOT both
#                   known to be self-reported. The zero-instrumentation default,
#                   and the coarse one: a webhook's ``created_at`` is whole
#                   seconds, so the number carries up to a second of truncation.
ANSWER_LATENCY_SOURCES: tuple[str, ...] = ("sipp_rtd", "agent_report", "webhook_proxy")

# Precedence. A weaker source never overwrites a stronger one, in ANY arrival
# order -- webhooks are unordered and redelivered, so a late proxy must not
# clobber the real number a load worker measured.
_ANSWER_LATENCY_RANK: dict[str, int] = {
    "webhook_proxy": 1,
    "agent_report": 2,
    "sipp_rtd": 3,
}

# The sources a caller may REPORT (see ``upsert_call``). Only ``sipp_rtd``: it is
# the only answer latency a process can measure end to end, because it placed the
# INVITE and saw the 200 OK. An agent never sees the 200 OK, so its contribution
# is its millisecond timestamps, which earn ``agent_report`` through the
# derivation rather than by asserting a duration.
REPORTED_ANSWER_LATENCY_SOURCES = frozenset({"sipp_rtd"})

# ``upsert_call_leg(source=...)`` values that count as an in-process observer,
# i.e. a real millisecond clock rather than a webhook's whole-second
# ``created_at``. These are the ``calls.origin`` names of the self-reporting
# writers; anything else (including NULL, "the writer did not say") is treated as
# webhook precision, which under-claims rather than over-claims.
SELF_REPORT_ORIGINS = frozenset({"agent", "loadgen"})

# Absurdity ceiling for an answer latency, in milliseconds. Five minutes is far
# longer than any caller waits and far longer than a carrier holds an INVITE, so
# anything above it is a clock disagreement or a unit bug (a seconds-scale or
# microsecond-scale timestamp), not a slow agent. Generous on purpose: this guard
# exists to catch broken inputs, not to censor bad performance.
_MAX_ANSWER_LATENCY_MS = 300_000


@dataclass(frozen=True)
class CallRow:
    """One ``calls`` row as served to readers."""

    id: str
    room_sid: str | None
    room_name: str | None
    origin: str
    attempt_id: str | None
    run_id: str | None
    project: str
    tenant_id: str | None
    agent_id: str | None
    channel: str | None
    direction: str | None
    started_at_ms: int | None
    ended_at_ms: int | None
    duration_ms: int | None
    end_reason: str | None
    num_legs: int
    is_probe: int
    answer_latency_ms: int | None
    answer_latency_source: str | None


@dataclass(frozen=True)
class CallLegRow:
    """One ``call_legs`` row as served to readers."""

    id: int | None
    call_id: str
    participant_sid: str
    identity: str | None
    kind: str | None
    region: str | None
    joined_at_ms: int | None
    left_at_ms: int | None
    disconnect_reason: str | None
    is_publisher: int | None
    attributes_json: str | None
    first_audio_track_at_ms: int | None
    audio_track_sid: str | None
    audio_codec: str | None
    # Who observed the two timestamps the answer-latency computation subtracts.
    # Served to readers so a rendered waterfall can say which clock it is drawing.
    joined_at_source: str | None
    first_audio_track_at_source: str | None


def _new_call_id() -> str:
    """Mint a call id. Generated here, not by the database, so the caller can
    write legs in the same event without a round-trip."""
    return uuid.uuid4().hex


def _call_row(call: Call) -> CallRow:
    return CallRow(
        id=call.id,
        room_sid=call.room_sid,
        room_name=call.room_name,
        origin=call.origin,
        attempt_id=call.attempt_id,
        run_id=call.run_id,
        project=call.project,
        tenant_id=call.tenant_id,
        agent_id=call.agent_id,
        channel=call.channel,
        direction=call.direction,
        started_at_ms=call.started_at_ms,
        ended_at_ms=call.ended_at_ms,
        duration_ms=call.duration_ms,
        end_reason=call.end_reason,
        num_legs=call.num_legs,
        is_probe=call.is_probe,
        answer_latency_ms=call.answer_latency_ms,
        answer_latency_source=call.answer_latency_source,
    )


def _leg_row(leg: CallLeg) -> CallLegRow:
    return CallLegRow(
        id=leg.id,
        call_id=leg.call_id,
        participant_sid=leg.participant_sid,
        identity=leg.identity,
        kind=leg.kind,
        region=leg.region,
        joined_at_ms=leg.joined_at_ms,
        left_at_ms=leg.left_at_ms,
        disconnect_reason=leg.disconnect_reason,
        is_publisher=leg.is_publisher,
        attributes_json=leg.attributes_json,
        first_audio_track_at_ms=leg.first_audio_track_at_ms,
        audio_track_sid=leg.audio_track_sid,
        audio_codec=leg.audio_codec,
        joined_at_source=leg.joined_at_source,
        first_audio_track_at_source=leg.first_audio_track_at_source,
    )


def _apply_present(row: Any, values: dict[str, Any]) -> None:
    """Set only the keys the event actually carried.

    ``None`` means "this event did not observe it", so it never overwrites a
    value another event already established. ``0`` and ``""`` are real
    observations and do get written.
    """
    for key, value in values.items():
        if value is None:
            continue
        setattr(row, key, value)


def _keep_earliest(row: Any, field: str, value: int | None) -> bool:
    """Keep the earliest of the stored and observed timestamps. True if written.

    The return value is what lets a leg's ``*_source`` column follow the value
    that actually won the merge instead of naming the last writer to try (see
    :func:`_apply_leg_event`). ``_keep_latest`` needs no such signal: nothing is
    derived from the provenance of an end timestamp.
    """
    if value is None:
        return False
    current = getattr(row, field)
    if current is None or value < current:
        setattr(row, field, value)
        return True
    return False


def _keep_latest(row: Any, field: str, value: int | None) -> None:
    """Keep the latest of the stored and observed timestamps."""
    if value is None:
        return
    current = getattr(row, field)
    if current is None or value > current:
        setattr(row, field, value)


def _flag(value: bool | int | None) -> int | None:
    """Normalize a tri-state flag to 0/1, preserving "not stated" as None."""
    return None if value is None else int(bool(value))


def _sip_attributes_json(attributes: dict[str, Any] | None) -> str | None:
    """Serialize the ``sip.*`` subset of a participant's attributes.

    Returns ``None`` when there are none, rather than ``"{}"``: an empty object
    would read as "we looked and the call had no SIP attributes", which is a
    different claim from "no attributes were reported".
    """
    if not attributes:
        return None
    sip_only = {
        key: value
        for key, value in attributes.items()
        if key.startswith(_SIP_ATTRIBUTE_PREFIX)
    }
    if not sip_only:
        return None
    return json.dumps(sip_only, sort_keys=True)


# ---------------------------------------------------------------------------
# The headline number: caller-visible answer latency
#
# ONE column, ONE computation. Nothing else in this product derives it, and no
# caller may pass a derived value in -- only a measured ``sipp_rtd`` may be
# reported (see ``upsert_call``).
# ---------------------------------------------------------------------------


def _answer_latency_rank(source: str | None) -> int:
    """Precedence of a source. 0 for "nothing stored" (and for an unknown name,
    which only a writer outside this module could have produced)."""
    if source is None:
        return 0
    return _ANSWER_LATENCY_RANK.get(source, 0)


def _plausible_answer_latency(span_ms: int) -> bool:
    """True when ``span_ms`` can be a caller's ring time.

    Rejects two shapes that are not measurements:

    * ``<= 0`` -- ``livekit-sip`` withholds ``200 OK`` until it subscribes to an
      audio track, so the publish CANNOT precede the caller's join. A
      non-positive interval means the two observations came from clocks that
      disagree, or that a whole-second webhook ``created_at`` truncated the later
      one below the earlier. Zero is included deliberately: publishing 0 would
      claim the caller heard no ring at all.
    * above :data:`_MAX_ANSWER_LATENCY_MS` -- a clock disagreement or a unit bug
      (seconds where milliseconds were meant, or the reverse), not a slow agent.
    """
    return 0 < span_ms <= _MAX_ANSWER_LATENCY_MS


def _earliest_with_source(
    legs: Sequence[CallLeg], kind: str, field: str, source_field: str
) -> tuple[int, str | None] | None:
    """``(timestamp, provenance)`` of the earliest ``kind`` leg to report ``field``.

    Earliest, because a transfer or SIP REFER can add a second leg of the same
    kind: the caller is the SIP leg that joined first, and the audio that
    released the ``200 OK`` is the first one an agent published.
    """
    best: tuple[int, str | None] | None = None
    for leg in legs:
        if leg.kind != kind:
            continue
        value = getattr(leg, field)
        if value is None:
            continue
        if best is None or value < best[0]:
            best = (int(value), getattr(leg, source_field))
    return best


def _derive_answer_latency(legs: Sequence[CallLeg]) -> tuple[int, str] | None:
    """THE computation: ``(answer_latency_ms, source)``, or None.

    ``agent first_audio_track_at_ms - caller joined_at_ms``. It is a proxy for
    caller-visible answer latency and a good one, because ``livekit-sip``
    withholds the ``200 OK`` until it has subscribed to an audio track: the
    agent's first published audio gates the caller's ring time. It costs zero
    instrumentation -- the webhooks alone produce it.

    Returns None, and the caller stores NULL, in every case where the number
    would not be sourced:

    * no SIP leg. A web or SIP-less participant is connected the instant it
      joins, so there is no ring to measure; the analogous number for it is
      time-to-first-audio, which is a different thing and must not be published
      under this name.
    * no SIP leg join time, no AGENT leg, or an agent leg that never published
      audio (a call that failed before answer -- the row that matters most in a
      load test, and it must not be given a fabricated latency).
    * legs whose ``kind`` was never observed: without it neither the caller nor
      the agent can be identified, and guessing would silently mix up the two.
    * an interval the clocks do not support (see
      :func:`_plausible_answer_latency`).

    The source names the WEAKER of the two inputs' clocks, never the better one:
    ``agent_report`` only when both timestamps were self-reported by a process
    inside the call, otherwise ``webhook_proxy``. A timestamp whose writer did not
    say is treated as webhook precision, so this under-claims rather than
    over-claims.
    """
    caller = _earliest_with_source(legs, _SIP_KIND, "joined_at_ms", "joined_at_source")
    published = _earliest_with_source(
        legs, _AGENT_KIND, "first_audio_track_at_ms", "first_audio_track_at_source"
    )
    if caller is None or published is None:
        return None

    span_ms = published[0] - caller[0]
    if not _plausible_answer_latency(span_ms):
        _logger.debug(
            "answer latency not derived: agent published at %d, caller joined at "
            "%d (span %dms is not a ring time)",
            published[0],
            caller[0],
            span_ms,
        )
        return None

    both_self_reported = (
        caller[1] in SELF_REPORT_ORIGINS and published[1] in SELF_REPORT_ORIGINS
    )
    return span_ms, "agent_report" if both_self_reported else "webhook_proxy"


def _apply_derived_answer_latency(call: Call, legs: Sequence[CallLeg]) -> None:
    """Re-derive the call's answer latency from its CURRENT merged leg state.

    The stored value is therefore a pure function of the merged legs, which is
    what makes every arrival order and every redelivery converge on the same
    number: the leg merge itself is order-independent (earliest join, earliest
    audio publish), so re-deriving after each leg write cannot depend on which
    webhook happened to arrive first.

    It also means a value the legs no longer support is CLEARED rather than left
    stale -- a later event carrying an earlier audio-publish timestamp can turn a
    plausible interval into an implausible one, and a reader must never be shown a
    latency that contradicts the leg timeline printed next to it.

    A REPORTED source is never touched here. ``sipp_rtd`` is the true INVITE->200
    wall time and outranks anything this derivation can produce, so a webhook
    arriving (or being redelivered) afterwards cannot clobber it.
    """
    if call.answer_latency_source in REPORTED_ANSWER_LATENCY_SOURCES:
        return
    derived = _derive_answer_latency(legs)
    if derived is None:
        call.answer_latency_ms = None
        call.answer_latency_source = None
        return
    call.answer_latency_ms, call.answer_latency_source = derived


def _validate_reported_answer_latency(
    reported_ms: int | None, reported_source: str | None
) -> None:
    """Raise on a report that must never reach the row.

    Called at the top of :func:`upsert_call`, before the row is created or
    modified, so a rejected event cannot leave a half-built ``Call`` pending in
    the session. Both of these are programming errors in the caller, not bad data
    from the wire (an ingest endpoint validates its payload at the edge).
    """
    if reported_ms is None and reported_source is None:
        return
    if reported_ms is None or reported_source is None:
        raise ValueError(
            "a reported answer latency needs both the value and its source: "
            "storing one without the other would publish a number nobody can "
            "attribute"
        )
    if reported_source not in REPORTED_ANSWER_LATENCY_SOURCES:
        raise ValueError(
            f"answer latency source {reported_source!r} cannot be reported; "
            f"reportable sources are {sorted(REPORTED_ANSWER_LATENCY_SOURCES)}. "
            f"{ANSWER_LATENCY_SOURCES[1]!r} and {ANSWER_LATENCY_SOURCES[2]!r} are "
            "derived from the leg timestamps, never asserted by a caller"
        )


def _apply_reported_answer_latency(
    call: Call, reported_ms: int | None, reported_source: str | None
) -> None:
    """Merge a validated caller-REPORTED answer latency under the precedence rule.

    An absent report means this event did not report one, which is not the same as
    reporting that there is none: nothing is written and nothing is cleared.

    A report that is not a plausible ring time is refused rather than stored, and
    refusing it also leaves whatever was already derived alone: a load worker
    reporting nonsense must not be able to erase a number the webhooks produced.
    """
    if reported_ms is None or reported_source is None:
        return
    if not _plausible_answer_latency(reported_ms):
        _logger.warning(
            "refusing a reported %s answer latency of %dms for call %s: outside "
            "0 < ms <= %d, so it is a clock or unit bug rather than a ring time",
            reported_source,
            reported_ms,
            call.id,
            _MAX_ANSWER_LATENCY_MS,
        )
        return
    if _answer_latency_rank(reported_source) < _answer_latency_rank(
        call.answer_latency_source
    ):
        return
    call.answer_latency_ms = reported_ms
    call.answer_latency_source = reported_source


async def _rederive_answer_latency(db: AsyncSession, call_id: str) -> None:
    """Re-derive one call's answer latency after a leg write.

    Called from :func:`upsert_call_leg` because the two timestamps the
    computation needs live on two DIFFERENT legs and arrive in any order, in any
    number of redeliveries.

    Concurrency: the leg written by this transaction is flushed before this read,
    so the derivation always sees it, and SQLite serializes writers so the second
    of two concurrent leg writes sees the first. Two transactions that CREATE the
    two different legs of one call in a genuine overlap can each see only their
    own leg, and then the value stays NULL until the next event for that call
    re-derives it (``participant_left`` arrives for every leg, so a real call
    always has one). NULL is the honest outcome of that window; a wrong number is
    not reachable from it.
    """
    call = (
        await db.execute(select(Call).where(Call.id == call_id))  # type: ignore[arg-type]
    ).scalar_one_or_none()
    if call is None:
        # Already logged by _refresh_num_legs: the call was pruned mid-flight, or
        # the id was invented. Keep the leg, derive nothing.
        return
    legs = (
        (
            await db.execute(
                select(CallLeg).where(CallLeg.call_id == call_id)  # type: ignore[arg-type]
            )
        )
        .scalars()
        .all()
    )
    _apply_derived_answer_latency(call, legs)


async def _select_by_keys(
    db: AsyncSession, room_sid: str | None, attempt_id: str | None
) -> Call | None:
    """Resolve the call this event belongs to, ``room_sid`` first.

    ``room_sid`` wins because it is the true per-instance key. Falling back to
    ``attempt_id`` is what merges a load generator's row (placed before any room
    existed) with the webhooks that follow.
    """
    if room_sid:
        # SQLModel types class attributes as their Python type, so the
        # comparison reads as `bool` to mypy; the ignores below are the same
        # ones every other repository in this package carries.
        stmt = select(Call).where(Call.room_sid == room_sid)  # type: ignore[arg-type]
        row = (await db.execute(stmt)).scalar_one_or_none()
        if row is not None:
            return row
    if attempt_id:
        stmt = select(Call).where(Call.attempt_id == attempt_id)  # type: ignore[arg-type]
        return (await db.execute(stmt)).scalar_one_or_none()
    return None


def _fill_keys(call: Call, room_sid: str | None, attempt_id: str | None) -> None:
    """Fill a unique key that is still NULL, never repoint one already set.

    A row found by ``attempt_id`` learns its ``room_sid`` here (the load
    generator placed the call, the webhook named the room). Overwriting a key
    that is already set would silently re-point the row at another call.
    """
    if room_sid and call.room_sid is None:
        call.room_sid = room_sid
    if attempt_id and call.attempt_id is None:
        call.attempt_id = attempt_id


def _apply_call_event(
    call: Call,
    *,
    values: dict[str, Any],
    started_at_ms: int | None,
    ended_at_ms: int | None,
    reported_answer_latency_ms: int | None = None,
    reported_answer_latency_source: str | None = None,
) -> None:
    """Merge one event into a call row and re-derive ``duration_ms``.

    The reported answer latency is applied here rather than in
    :func:`upsert_call` so the IntegrityError retry path merges it too: an event
    that lost a race on a unique key must not silently drop the one number the
    load worker actually measured.
    """
    _apply_present(call, values)
    _apply_reported_answer_latency(
        call, reported_answer_latency_ms, reported_answer_latency_source
    )
    _keep_earliest(call, "started_at_ms", started_at_ms)
    _keep_latest(call, "ended_at_ms", ended_at_ms)
    if call.started_at_ms is not None and call.ended_at_ms is not None:
        span = call.ended_at_ms - call.started_at_ms
        # A negative span means the two ends came from clocks that disagree.
        # Leave the column NULL rather than publish a 0 that reads as "instant".
        if span >= 0:
            call.duration_ms = span


async def upsert_call(
    db: AsyncSession,
    *,
    origin: str,
    room_sid: str | None = None,
    attempt_id: str | None = None,
    room_name: str | None = None,
    run_id: str | None = None,
    project: str | None = None,
    tenant_id: str | None = None,
    agent_id: str | None = None,
    channel: str | None = None,
    direction: str | None = None,
    started_at_ms: int | None = None,
    ended_at_ms: int | None = None,
    end_reason: str | None = None,
    is_probe: bool | int | None = None,
    reported_answer_latency_ms: int | None = None,
    reported_answer_latency_source: str | None = None,
) -> str:
    """Create or merge one call from a single event; return its ``calls.id``.

    ``origin`` is the writer of this event (``webhook`` | ``loadgen`` |
    ``agent``); it is recorded when the row is created and not rewritten
    afterwards, so a load attempt later enriched by webhooks is still a
    ``loadgen`` call.

    At least one of ``room_sid`` / ``attempt_id`` is required: an event with no
    key cannot be deduplicated, and accepting one would create a fresh row per
    redelivery. ``room_name`` is deliberately not a key -- a deployment that pins
    one fixed room name would collapse every concurrent call into one row.

    ``duration_ms``, ``num_legs`` and ``answer_latency_ms`` are derived (here and
    in :func:`upsert_call_leg`) and cannot be passed in.

    ``reported_answer_latency_ms`` / ``reported_answer_latency_source`` are the
    one exception, and only for a latency the caller MEASURED itself: the source
    must be in :data:`REPORTED_ANSWER_LATENCY_SOURCES` (``sipp_rtd``, a load
    worker's true INVITE->200 wall time), both must be given together, and the
    value is refused if it is not a plausible ring time. It outranks the derived
    proxy and cannot be overwritten by it, in any arrival order.
    """
    if not room_sid and not attempt_id:
        raise ValueError(
            "upsert_call needs room_sid or attempt_id: an event with neither "
            "cannot be correlated, and would create a new row per redelivery"
        )
    _validate_reported_answer_latency(
        reported_answer_latency_ms, reported_answer_latency_source
    )

    values: dict[str, Any] = {
        "room_name": room_name,
        "run_id": run_id,
        "project": project,
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "channel": channel,
        "direction": direction,
        "end_reason": end_reason,
        "is_probe": _flag(is_probe),
    }

    call = await _select_by_keys(db, room_sid, attempt_id)
    if call is None:
        call = Call(
            id=_new_call_id(),
            origin=origin,
            project=project or DEFAULT_PROJECT,
            room_sid=room_sid,
            attempt_id=attempt_id,
        )
        db.add(call)
    else:
        _fill_keys(call, room_sid, attempt_id)
    # Read the id before committing: with expire_on_commit the attribute would
    # need a lazy refresh afterwards, which raises on an async session.
    call_id = call.id
    _apply_call_event(
        call,
        values=values,
        started_at_ms=started_at_ms,
        ended_at_ms=ended_at_ms,
        reported_answer_latency_ms=reported_answer_latency_ms,
        reported_answer_latency_source=reported_answer_latency_source,
    )

    try:
        await db.commit()
    except IntegrityError:
        # Either a concurrent first insert of the same key, or the *other*
        # unique key is already held by a different row (the load generator and
        # the webhook receiver can each create a row before either learns the
        # other's key). Re-resolve and merge the non-key fields: two rows for one
        # call is a correlation gap, but dropping an ingest event or deleting a
        # row here would be worse.
        await db.rollback()
        call = await _select_by_keys(db, room_sid, attempt_id)
        if call is None:
            raise
        call_id = call.id
        _apply_call_event(
            call,
            values=values,
            started_at_ms=started_at_ms,
            ended_at_ms=ended_at_ms,
            reported_answer_latency_ms=reported_answer_latency_ms,
            reported_answer_latency_source=reported_answer_latency_source,
        )
        await db.commit()
    return call_id


async def _refresh_num_legs(db: AsyncSession, call_id: str) -> None:
    """Recount the call's legs in ONE statement.

    Derived, so redelivery cannot inflate it. Counting inside the same UPDATE
    that assigns is what makes it correct under concurrent delivery: a
    count-then-assign lets two interleaved leg writes for one call both observe
    the pre-insert total and stamp the same stale number, which was measured at
    0-3 calls per 300 with 20 webhooks in flight.

    ``synchronize_session="fetch"`` matters because the session is built with
    ``expire_on_commit=False`` (``core/database.py``), so a bare Core UPDATE
    would leave an already-loaded Call carrying the old count.
    """
    count_sq = (
        select(func.count())
        .select_from(CallLeg)
        .where(CallLeg.call_id == call_id)  # type: ignore[arg-type]
        .scalar_subquery()
    )
    result = await db.execute(
        update(Call)
        .where(Call.id == call_id)  # type: ignore[arg-type]
        .values(num_legs=count_sq)
        .execution_options(synchronize_session="fetch")
    )
    if (result.rowcount or 0) == 0:  # type: ignore[attr-defined]
        # Callers take call_id from upsert_call, which creates the row from any
        # event, so this means the call was pruned mid-flight or the id was
        # invented. Keep the leg and say so, rather than raise in an ingest path.
        _logger.warning("call_legs row written for unknown call_id=%s", call_id)


def _apply_leg_event(
    leg: CallLeg,
    *,
    values: dict[str, Any],
    joined_at_ms: int | None,
    left_at_ms: int | None,
    first_audio_track_at_ms: int | None,
    source: str | None = None,
) -> None:
    """Merge one event into a leg row, stamping who observed each timestamp.

    The two ``*_source`` columns describe THE VALUE THAT IS STORED, not the last
    writer to try: they are set only when this event's timestamp actually won the
    merge. So if a webhook's whole-second ``created_at`` undercuts an agent's
    millisecond observation, the stored value is webhook-precision and the column
    says ``webhook`` -- which keeps ``calls.answer_latency_source`` reproducible
    from the leg rows a reader is shown next to it.
    """
    _apply_present(leg, values)
    if _keep_earliest(leg, "joined_at_ms", joined_at_ms):
        leg.joined_at_source = source
    _keep_latest(leg, "left_at_ms", left_at_ms)
    # "First" audio track: the earliest publish we ever saw, not the newest.
    if _keep_earliest(leg, "first_audio_track_at_ms", first_audio_track_at_ms):
        leg.first_audio_track_at_source = source


async def upsert_call_leg(
    db: AsyncSession,
    *,
    call_id: str,
    participant_sid: str,
    identity: str | None = None,
    kind: str | None = None,
    region: str | None = None,
    joined_at_ms: int | None = None,
    left_at_ms: int | None = None,
    disconnect_reason: str | None = None,
    is_publisher: bool | int | None = None,
    attributes: dict[str, Any] | None = None,
    first_audio_track_at_ms: int | None = None,
    audio_track_sid: str | None = None,
    audio_codec: str | None = None,
    source: str | None = None,
) -> None:
    """Create or merge one participant leg, keyed ``(call_id, participant_sid)``.

    ``participant_sid`` is required and never NULL: it is half of the unique key,
    and a NULL there is distinct from every other NULL on both engines, so a
    redelivered webhook would insert a second leg instead of updating the first.

    Only the ``sip.*`` keys of ``attributes`` are stored. ``calls.num_legs`` and
    ``calls.answer_latency_ms`` are both re-derived from this table in the same
    transaction.

    ``source`` is the ORIGIN OF THIS EVENT'S WRITER -- the same vocabulary as
    ``calls.origin`` (``webhook`` | ``agent`` | ``loadgen``) -- and is stored only
    as the provenance of the timestamps this event wins. Pass one of
    :data:`SELF_REPORT_ORIGINS` and a pair of self-reported timestamps earns the
    stronger ``agent_report`` answer-latency source; leave it ``None`` (the
    writer did not say) and the derivation assumes whole-second webhook
    precision, which under-claims rather than over-claims.
    """
    values: dict[str, Any] = {
        "identity": identity,
        "kind": kind,
        "region": region,
        "disconnect_reason": disconnect_reason,
        "is_publisher": _flag(is_publisher),
        "attributes_json": _sip_attributes_json(attributes),
        "audio_track_sid": audio_track_sid,
        "audio_codec": audio_codec,
    }
    stmt = select(CallLeg).where(
        CallLeg.call_id == call_id,  # type: ignore[arg-type]
        CallLeg.participant_sid == participant_sid,  # type: ignore[arg-type]
    )
    leg = (await db.execute(stmt)).scalar_one_or_none()
    if leg is None:
        leg = CallLeg(call_id=call_id, participant_sid=participant_sid)
        db.add(leg)
    _apply_leg_event(
        leg,
        values=values,
        joined_at_ms=joined_at_ms,
        left_at_ms=left_at_ms,
        first_audio_track_at_ms=first_audio_track_at_ms,
        source=source,
    )

    try:
        await db.flush()
    except IntegrityError:
        # A concurrent first insert of the same (call_id, participant_sid).
        await db.rollback()
        leg = (await db.execute(stmt)).scalar_one()
        _apply_leg_event(
            leg,
            values=values,
            joined_at_ms=joined_at_ms,
            left_at_ms=left_at_ms,
            first_audio_track_at_ms=first_audio_track_at_ms,
            source=source,
        )
    await _refresh_num_legs(db, call_id)
    # After the flush, so the leg written above is part of the derivation, and
    # after the recount, so both derived columns land in one commit.
    await _rederive_answer_latency(db, call_id)
    await db.commit()


async def get_call(db: AsyncSession, call_id: str) -> CallRow | None:
    """One call by id, or None."""
    stmt = select(Call).where(Call.id == call_id)  # type: ignore[arg-type]
    call = (await db.execute(stmt)).scalar_one_or_none()
    return None if call is None else _call_row(call)


async def get_call_by_room_sid(db: AsyncSession, room_sid: str) -> CallRow | None:
    """One call by LiveKit room SID (the true per-instance key), or None."""
    stmt = select(Call).where(Call.room_sid == room_sid)  # type: ignore[arg-type]
    call = (await db.execute(stmt)).scalar_one_or_none()
    return None if call is None else _call_row(call)


async def list_calls(
    db: AsyncSession,
    *,
    limit: int = 100,
    project: str | None = None,
    run_id: str | None = None,
    tenant: str | None = None,
    is_probe: bool | None = False,
) -> list[CallRow]:
    """Newest calls first.

    ``is_probe`` defaults to ``False`` -- **load-test traffic is excluded unless
    asked for**, so a caller computing production numbers cannot silently mix
    synthetic load into them. Pass ``True`` for load traffic only, ``None`` for
    every row.

    ``tenant`` is a read SCOPE, not a column value, which is why it is not named
    ``tenant_id`` like the write path's argument. It carries the same three-way
    convention every sibling read uses
    (``session_repository.list_sessions``, ``cost_repository``,
    ``request_log_repository``, ``latency_repository``):

    * ``None`` -- every tenant. The operator/admin case.
    * ``""`` -- the unattributed bucket, i.e. ``tenant_id IS NULL``.
    * anything else -- that tenant's rows only.

    The predicate belongs HERE and not in the caller: filtering a page after it
    has been read makes ``limit`` a bound on rows *scanned* rather than rows
    *returned*, so a tenant-scoped reader asking for 50 gets however many of its
    own calls happened to fall inside the newest 50 overall -- possibly none,
    while more of its own sit one row past the boundary.

    The ``""`` case is spelled ``IS NULL`` explicitly rather than left to SQL's
    three-valued logic: ``tenant_id = ''`` matches no NULL row on SQLite or
    PostgreSQL, so the unattributed reader would silently be served an empty
    page instead of its own calls.

    The explicit ``started_at_ms IS NULL`` sort key puts a start-less row (an
    INVITE that never produced a room) last on SQLite and PostgreSQL alike: the
    two disagree on where a bare ``ORDER BY ... DESC`` puts NULLs. ``id`` is the
    stable tiebreak.
    """
    stmt = select(Call)
    if project is not None:
        stmt = stmt.where(Call.project == project)  # type: ignore[arg-type]
    if run_id is not None:
        stmt = stmt.where(Call.run_id == run_id)  # type: ignore[arg-type]
    if tenant is not None:
        if tenant == "":
            stmt = stmt.where(Call.tenant_id.is_(None))  # type: ignore[union-attr]
        else:
            stmt = stmt.where(Call.tenant_id == tenant)  # type: ignore[arg-type]
    if is_probe is not None:
        stmt = stmt.where(Call.is_probe == int(is_probe))  # type: ignore[arg-type]
    stmt = stmt.order_by(
        Call.started_at_ms.is_(None).asc(),  # type: ignore[union-attr]
        Call.started_at_ms.desc(),  # type: ignore[union-attr]
        Call.id.desc(),  # type: ignore[attr-defined]
    ).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return [_call_row(call) for call in rows]


async def list_call_legs(db: AsyncSession, call_id: str) -> list[CallLegRow]:
    """One call's legs, earliest join first.

    A leg whose join time was never observed sorts last (same dialect-neutral
    ``IS NULL`` sort key as :func:`list_calls`), with ``id`` as the tiebreak.
    """
    stmt = (
        select(CallLeg)
        .where(CallLeg.call_id == call_id)  # type: ignore[arg-type]
        .order_by(
            CallLeg.joined_at_ms.is_(None).asc(),  # type: ignore[union-attr]
            CallLeg.joined_at_ms.asc(),  # type: ignore[union-attr]
            CallLeg.id.asc(),  # type: ignore[union-attr]
        )
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [_leg_row(leg) for leg in rows]


__all__ = [
    "ANSWER_LATENCY_SOURCES",
    "REPORTED_ANSWER_LATENCY_SOURCES",
    "SELF_REPORT_ORIGINS",
    "CallLegRow",
    "CallRow",
    "DEFAULT_PROJECT",
    "get_call",
    "get_call_by_room_sid",
    "list_call_legs",
    "list_calls",
    "upsert_call",
    "upsert_call_leg",
]
