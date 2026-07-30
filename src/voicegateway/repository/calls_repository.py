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

Forward-only: nothing here backfills, repairs, or scans history.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
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


def _keep_earliest(row: Any, field: str, value: int | None) -> None:
    """Keep the earliest of the stored and observed timestamps."""
    if value is None:
        return
    current = getattr(row, field)
    if current is None or value < current:
        setattr(row, field, value)


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
) -> None:
    """Merge one event into a call row and re-derive ``duration_ms``."""
    _apply_present(call, values)
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

    ``duration_ms`` and ``num_legs`` are derived (here and in
    :func:`upsert_call_leg`) and cannot be passed in.
    """
    if not room_sid and not attempt_id:
        raise ValueError(
            "upsert_call needs room_sid or attempt_id: an event with neither "
            "cannot be correlated, and would create a new row per redelivery"
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
        call, values=values, started_at_ms=started_at_ms, ended_at_ms=ended_at_ms
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
            call, values=values, started_at_ms=started_at_ms, ended_at_ms=ended_at_ms
        )
        await db.commit()
    return call_id


async def _refresh_num_legs(db: AsyncSession, call_id: str) -> None:
    """Recount the call's legs. Derived, so redelivery cannot inflate it."""
    count_stmt = (
        select(func.count())
        .select_from(CallLeg)
        .where(CallLeg.call_id == call_id)  # type: ignore[arg-type]
    )
    count = int((await db.execute(count_stmt)).scalar_one() or 0)
    call = (
        await db.execute(
            select(Call).where(Call.id == call_id)  # type: ignore[arg-type]
        )
    ).scalar_one_or_none()
    if call is None:
        # Callers take call_id from upsert_call, which creates the row from any
        # event, so this means the call was pruned mid-flight or the id was
        # invented. Keep the leg and say so, rather than raise in an ingest path.
        _logger.warning("call_legs row written for unknown call_id=%s", call_id)
        return
    call.num_legs = count


def _apply_leg_event(
    leg: CallLeg,
    *,
    values: dict[str, Any],
    joined_at_ms: int | None,
    left_at_ms: int | None,
    first_audio_track_at_ms: int | None,
) -> None:
    """Merge one event into a leg row."""
    _apply_present(leg, values)
    _keep_earliest(leg, "joined_at_ms", joined_at_ms)
    _keep_latest(leg, "left_at_ms", left_at_ms)
    # "First" audio track: the earliest publish we ever saw, not the newest.
    _keep_earliest(leg, "first_audio_track_at_ms", first_audio_track_at_ms)


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
) -> None:
    """Create or merge one participant leg, keyed ``(call_id, participant_sid)``.

    ``participant_sid`` is required and never NULL: it is half of the unique key,
    and a NULL there is distinct from every other NULL on both engines, so a
    redelivered webhook would insert a second leg instead of updating the first.

    Only the ``sip.*`` keys of ``attributes`` are stored. ``calls.num_legs`` is
    recounted from this table in the same transaction.
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
        )
    await _refresh_num_legs(db, call_id)
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
    is_probe: bool | None = False,
) -> list[CallRow]:
    """Newest calls first.

    ``is_probe`` defaults to ``False`` -- **load-test traffic is excluded unless
    asked for**, so a caller computing production numbers cannot silently mix
    synthetic load into them. Pass ``True`` for load traffic only, ``None`` for
    every row.

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
