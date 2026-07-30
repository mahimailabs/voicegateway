"""POST /v1/calls/observations: the per-call view only the caller-side process has.

Why this endpoint exists
-----------------------
Some of a call's timeline is visible **only** from inside the process that took
part in it. LiveKit sends no ``track_subscribed`` webhook, and the webhook
``created_at`` is whole *seconds*, so the agent's own clock is the only source of
a millisecond-precision "I published audio at T" -- the timestamp that gates the
caller's ring time, because ``livekit-sip`` withholds ``200 OK`` until it
subscribes to an audio track. An agent (or a load worker) posts what it saw
here; everything else about the call still arrives from
``/v1/livekit/webhook``, and both writers merge into the same rows through
:mod:`voicegateway.repository.calls_repository`.

Why it is fire-and-forget, and what that costs
----------------------------------------------
The reporting hook runs in the agent's job-start path -- the exact path being
measured. A synchronous POST that waits for a SQLite write would add its own
latency to the number it is reporting, so:

* the handler **validates, enqueues, and returns 202**. It never awaits the
  database. (:func:`post_call_observation` contains no ``await`` on storage; the
  test suite pins this by blocking the write and asserting the POST still
  returns.)
* the queue is **bounded** at :data:`_QUEUE_MAXSIZE` items. When it is full the
  newest report is **dropped**, the ``dropped_total`` counter is incremented and
  the response says ``"status": "dropped"`` with HTTP **429**. Shedding load is
  the point: blocking the reporter, or growing the queue without limit, would
  make an overloaded collector degrade the thing it is measuring. A 2xx for a
  report we threw away would be a quiet lie, hence 429.
* **one** background flusher drains the queue, sequentially, for the whole
  process. Not a task per request: 500 concurrent calls would otherwise mean 500
  tasks contending for one SQLite writer.
* ``VG_DISABLE_CALL_OBSERVATIONS`` turns the whole path off (503, nothing
  enqueued, no flusher started), read per request so it takes effect without a
  restart.
* nothing in the write path is allowed to escape: a failed item is counted and
  logged, and the flusher survives it. If the flusher ever dies, the queue fills
  and every later report is dropped -- so it does not die.

Observing the counters: every response carries ``queue_depth`` and
``dropped_total``, :func:`call_observation_stats` returns
``queue_depth`` / ``dropped_total`` / ``written_total`` / ``failed_total`` /
``flusher_running`` for the process, and each drop logs a WARNING (the first,
then every 100th) so the gap is visible in the log as well as in the response.

Auth: the standard ``require_scope("write")``, declared **once on the router**
Unlike the LiveKit webhook -- which LiveKit posts and cannot sign with a
VoiceGateway key -- this endpoint is called by the operator's own agents and load
workers, which already carry ``VOICEGW_API_KEY`` for ``/v1/ingest``. So it takes
the same dependency every other mutating ``/v1`` route takes. It is declared on
the ``APIRouter`` rather than per handler on purpose: a router whose handlers
each opt in individually is one forgotten decorator away from an unauthenticated
write endpoint, which is exactly how this server ended up with two unguarded
key-minting routers. This router is **write-only** for that reason -- a future
reader must not inherit ``write`` from here, so it gets its own router.

``tenant_id`` is stamped server-side from the verified key
(``request.state.api_key_tenant_id``), never read from the payload, matching
``/v1/ingest``. It is captured at enqueue time and travels **on the queued item**
because ContextVars do not follow work into another task.

What is accepted, and why each field is observable
--------------------------------------------------
Every accepted field is something the reporting process itself saw, and every
one has a column that already exists (``calls`` / ``call_legs``, from T1):

* ``origin`` -- ``agent`` | ``loadgen``, who is reporting. ``webhook`` is
  refused: only the signature-verified receiver may claim to be one.
* ``room_sid`` / ``room_name`` -- ``rtc.Room.sid`` / ``.name``.
* ``attempt_id`` / ``run_id`` -- the load worker's own correlation ids.
* ``project`` / ``agent_id`` -- the reporter's own VoiceGateway labels.
* ``started_at_ms`` / ``ended_at_ms`` -- the reporter's view of when the **call**
  started and ended (``rtc.Room.creation_time``, or the INVITE a load worker
  placed and the BYE it saw). NOT the reporter's own job lifetime: a leg's
  lifetime belongs on the leg. The repository keeps the earliest start and the
  latest end, so order of arrival does not matter.
* ``legs[].participant_sid`` -- ``rtc.Participant.sid``. Required: it is half of
  the leg's unique key and must never be NULL.
* ``legs[].identity`` / ``legs[].kind`` -- ``.identity`` / ``.kind``.
* ``legs[].joined_at_ms`` -- ``.joined_at``.
* ``legs[].left_at_ms`` / ``legs[].disconnect_reason`` -- when this leg went
  away, and ``.disconnect_reason``.
* ``legs[].first_audio_track_at_ms`` -- when this leg's first audio publication
  was observed, at the reporter's millisecond precision. The highest-value field
  here: the webhook can only see it to the nearest second.
* ``legs[].audio_track_sid`` / ``legs[].audio_codec`` --
  ``RemoteTrackPublication.sid`` / ``.mime_type``.

``calls.channel`` and ``calls.end_reason`` are **derived from the reported legs
by the same rule the webhook receiver uses** (channel is only ever ``sip``, never
``web``; the call's end reason comes only from a SIP leg, because that leg is the
caller) so there is one rule, not two.

Unknown fields are **rejected** (``extra="forbid"`` -> 422). That is deliberate:
silently ignoring a field would let a reporter believe a number was stored when
nothing stores it. The fields that would be ignored, and are therefore refused:

* ``is_probe`` -- D0 decision 1: probe traffic is discriminated by a dedicated
  load-test trunk, never by anything on the wire, because a forged flag would
  hide real traffic from production percentiles.
* ``tenant_id`` -- stamped from the API key (see above).
* per-call **RTP loss / jitter / MOS / DTMF** -- not observable server-side, no
  column reserved, and an empty column invites a UI that renders ``0.0`` as
  "no loss".
* per-call **SIP response codes** -- ``livekit-api 1.1.0`` has no
  ``ListSIPCallInfo``, so nothing here pretends to carry one.
* **a reported answer latency** (``sipp_rtd`` INVITE->200 wall time) and
  **subscribe latency** -- see the boundary note below.

The T5 boundary, stated plainly
-------------------------------
``answer_latency_ms`` / ``answer_latency_source`` are **not written here**.
``upsert_call`` takes no such argument, and choosing whether a self-report may
overwrite a stored value is the precedence rule that T5 owns
(``sipp_rtd`` > agent self-report > webhook proxy): one number, one computation.
What this endpoint contributes to it is the pair of timestamps the computation
needs, at agent precision -- the agent leg's ``first_audio_track_at_ms`` and the
caller leg's ``joined_at_ms``. A reported *duration* (a load worker's true
INVITE->200 RTD, or an agent's subscribe latency) has **no column in the M1
schema**, so it is refused rather than silently dropped; giving it a home is a
schema change, and the schema is T5's file, not this one's.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from voicegateway.server.api._deps import get_gateway, require_scope

if TYPE_CHECKING:  # pragma: no cover - typing only
    from voicegateway.services.storage_service import StorageService

logger = logging.getLogger(__name__)

# Auth is declared ONCE, here, for every route on this router: see the module
# docstring. This router is write-only; readers get their own router so they
# never inherit the write scope.
router = APIRouter(
    prefix="/calls",
    tags=["calls"],
    dependencies=[Depends(require_scope("write"))],
)

# The kill switch. Any value other than an explicit falsy word disables the
# path, so `VG_DISABLE_CALL_OBSERVATIONS=1` and `=yes` both work.
_KILL_SWITCH_ENV = "VG_DISABLE_CALL_OBSERVATIONS"
_FALSY = frozenset({"", "0", "false", "no", "off"})

# The bound. 1000 reports is a few hundred KB of dataclasses and, at the ~700
# writes/s the SQLite writer sustained under T2's burst measurement, about 1.4 s
# of backlog: long enough to absorb a burst, short enough that a wedged writer
# is noticed as drops rather than as unbounded memory growth.
_QUEUE_MAXSIZE = 1000

# Per-report leg cap. A call has a caller leg and an agent leg, sometimes a
# transfer or SIP REFER leg; 16 is generous. It exists so one report cannot turn
# into an unbounded number of writes in the flusher.
_MAX_LEGS = 16

# Millisecond-scale guard. 1e12 ms is 2001-09-09 and 1e13 ms is the year 2286,
# so a value outside this range is a unit bug (seconds below, micro/nanoseconds
# above) rather than a timestamp. Catching it here keeps a seconds-scale value
# from being merged as an epoch-ms `started_at_ms` and publishing a ~55-year
# `duration_ms`.
_MIN_EPOCH_MS = 1_000_000_000_000
_MAX_EPOCH_MS = 10_000_000_000_000

# Log the first drop, then every hundredth: a 500-call burst that overflows must
# not also flood the log it is being diagnosed from.
_DROP_LOG_EVERY = 100

# Longest a shutdown will wait for the queue to drain before saying what is lost.
_DRAIN_TIMEOUT_SECONDS = 5.0

_FLUSHER_TASK_NAME = "voicegw-call-observations-flusher"

_Id = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)
]
_Label = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
]
_EpochMs = Annotated[int, Field(ge=_MIN_EPOCH_MS, le=_MAX_EPOCH_MS)]
# The ParticipantInfo.Kind names LiveKit uses, as the SDK reports them.
_Kind = Literal["SIP", "AGENT", "STANDARD", "INGRESS", "EGRESS"]


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------


class LegObservation(BaseModel):
    """One participant's timeline as the reporting process observed it."""

    model_config = ConfigDict(extra="forbid")

    participant_sid: _Id
    identity: _Id | None = None
    kind: _Kind | None = None
    joined_at_ms: _EpochMs | None = None
    left_at_ms: _EpochMs | None = None
    disconnect_reason: _Label | None = None
    # When this leg's first audio publication was observed. For the agent's own
    # leg this is the number the webhook can only see to the nearest second.
    first_audio_track_at_ms: _EpochMs | None = None
    audio_track_sid: _Id | None = None
    audio_codec: _Label | None = None


class CallObservation(BaseModel):
    """One call as an agent or load worker observed it.

    Unknown fields are refused rather than ignored -- see the module docstring
    for the list of things that would otherwise be silently dropped.
    """

    model_config = ConfigDict(extra="forbid")

    origin: Literal["agent", "loadgen"]
    room_sid: _Id | None = None
    room_name: _Id | None = None
    attempt_id: _Id | None = None
    run_id: _Id | None = None
    project: _Id | None = None
    agent_id: _Id | None = None
    started_at_ms: _EpochMs | None = None
    ended_at_ms: _EpochMs | None = None
    legs: list[LegObservation] = Field(default_factory=list, max_length=_MAX_LEGS)

    @model_validator(mode="after")
    def _require_a_correlation_key(self) -> CallObservation:
        """Refuse a report with no key, at the edge rather than in the flusher.

        ``upsert_call`` raises on a keyless event because it would create a fresh
        row per redelivery. Checking here turns that into a 422 the reporter can
        see, instead of an item that is accepted with a 202 and then dropped
        inside the background task.
        """
        if not self.room_sid and not self.attempt_id:
            raise ValueError(
                "a call observation needs room_sid or attempt_id: a report with "
                "neither cannot be correlated to a call"
            )
        return self


# ---------------------------------------------------------------------------
# Queue + single flusher
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _QueuedObservation:
    """One accepted report, plus everything the flusher needs to write it.

    ``storage`` and ``tenant_id`` ride along rather than being looked up in the
    flusher: the storage handle belongs to the app that accepted the report (a
    process can build more than one), and the tenant was resolved from the
    verified API key in the request's own context, which a background task does
    not inherit.
    """

    storage: StorageService
    observation: CallObservation
    tenant_id: str | None


@dataclass
class _FlusherState:
    """The queue, the one flusher task, and the counters, for one event loop.

    Keyed on the loop because an ``asyncio.Queue`` binds to the loop that first
    uses it and raises if a second one touches it. In production there is one
    loop for the life of the process, so this is a per-process singleton; in the
    test suite each test gets a fresh loop and therefore fresh state.
    """

    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[_QueuedObservation]
    task: asyncio.Task[None] | None = field(default=None)
    dropped: int = 0
    written: int = 0
    failed: int = 0


_state: _FlusherState | None = None


def _disabled() -> bool:
    """True when the kill switch is set. Read per request, not cached."""
    raw = (os.environ.get(_KILL_SWITCH_ENV) or "").strip().lower()
    return raw not in _FALSY


def _state_for_running_loop() -> _FlusherState:
    """The queue/flusher state for the running loop, created on first use."""
    global _state
    loop = asyncio.get_running_loop()
    state = _state
    if state is None or state.loop is not loop:
        if state is not None and state.queue.qsize():
            # A second loop took over the process (two apps run one after the
            # other). The old queue cannot be touched from here, so say what
            # went with it instead of losing it quietly.
            logger.warning(
                "call observations: a new event loop took over with %d report(s) "
                "still queued on the previous one; they are lost",
                state.queue.qsize(),
            )
        state = _FlusherState(loop=loop, queue=asyncio.Queue(maxsize=_QUEUE_MAXSIZE))
        _state = state
    return state


def _ensure_flusher(state: _FlusherState) -> None:
    """Start the ONE flusher for this loop, if it is not already running.

    The task is created with an **empty** context on purpose. A task inherits a
    copy of whichever context created it, so a lazily started flusher would
    otherwise carry the ContextVars (``session_id``, ``tenant``) of whatever
    request happened to be first, and every later item would be written under
    that one request's context. Each item carries its own ``tenant_id``
    explicitly instead.

    A task that has finished (only possible via cancellation, since the flush
    loop swallows item failures) is replaced, so the endpoint self-heals rather
    than silently dropping everything from then on.
    """
    task = state.task
    if task is not None and not task.done():
        return
    if task is not None:
        logger.warning("call observations: flusher had stopped; restarting it")
    state.task = state.loop.create_task(
        _flush_forever(state),
        name=_FLUSHER_TASK_NAME,
        context=contextvars.Context(),
    )


async def _flush_forever(state: _FlusherState) -> None:
    """Drain the queue for the life of the process, one report at a time.

    Sequential by design: the point of a single flusher is that a 500-call burst
    does not put 500 tasks in front of one SQLite writer.

    This coroutine must not raise. An item that fails to write is counted and
    logged; if this loop ever exited, the queue would fill and every later
    report would be dropped.

    Every value that varies per report (the storage handle, the tenant) is read
    off the item, never from a ContextVar: this task runs in ONE context for the
    life of the process, so a per-request value picked up here would be applied
    to every later report. Nothing on the write path (``calls_repository``) reads
    or sets a ContextVar today, and nothing added to it may.
    """
    while True:
        item = await state.queue.get()
        try:
            await _write_observation(item)
            state.written += 1
        except asyncio.CancelledError:
            # Shutdown arrived mid-write. This one report is lost; say so rather
            # than counting it as written.
            state.failed += 1
            logger.warning(
                "call observations: cancelled while writing a report; it is lost"
            )
            raise
        except Exception:  # noqa: BLE001 - one bad report must not stop the flusher
            state.failed += 1
            logger.warning(
                "call observations: failed to persist a %s report (room_sid=%r "
                "attempt_id=%r); dropping it",
                item.observation.origin,
                item.observation.room_sid,
                item.observation.attempt_id,
                exc_info=True,
            )
        finally:
            state.queue.task_done()


def _channel(legs: list[LegObservation]) -> str | None:
    """``'sip'`` when a reported leg is a SIP leg, else None.

    Never ``'web'``: the same rule as the webhook receiver, so a later report
    that cannot see the SIP leg does not clobber the channel of a call we already
    knew had one.
    """
    return "sip" if any(leg.kind == "SIP" for leg in legs) else None


def _end_reason(legs: list[LegObservation]) -> str | None:
    """The call's end reason: the SIP leg's, or None.

    Mirrors the webhook rule exactly. The SIP leg is the caller, so its
    disconnect reason is why the call ended as the caller experienced it; an
    agent leg's reason stays on its own leg row.
    """
    for leg in legs:
        if leg.kind == "SIP" and leg.disconnect_reason:
            return leg.disconnect_reason
    return None


async def _write_observation(item: _QueuedObservation) -> None:
    """Merge one report into ``calls`` (+ ``call_legs``).

    Every value passed is one the reporter actually observed; anything it did not
    observe is ``None``, which the repository treats as "did not say" rather than
    "set back to unknown". ``is_probe`` is deliberately absent: it is derived from
    the SIP trunk, never from the wire.
    """
    obs = item.observation
    call_id = await item.storage.upsert_call(
        origin=obs.origin,
        room_sid=obs.room_sid,
        attempt_id=obs.attempt_id,
        room_name=obs.room_name,
        run_id=obs.run_id,
        project=obs.project,
        tenant_id=item.tenant_id,
        agent_id=obs.agent_id,
        channel=_channel(obs.legs),
        started_at_ms=obs.started_at_ms,
        ended_at_ms=obs.ended_at_ms,
        end_reason=_end_reason(obs.legs),
    )
    for leg in obs.legs:
        try:
            await item.storage.upsert_call_leg(
                call_id=call_id,
                participant_sid=leg.participant_sid,
                identity=leg.identity,
                kind=leg.kind,
                joined_at_ms=leg.joined_at_ms,
                left_at_ms=leg.left_at_ms,
                disconnect_reason=leg.disconnect_reason,
                first_audio_track_at_ms=leg.first_audio_track_at_ms,
                audio_track_sid=leg.audio_track_sid,
                audio_codec=leg.audio_codec,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one bad leg must not lose its siblings
            logger.warning(
                "call observations: failed to persist leg %r of call %s",
                leg.participant_sid,
                call_id,
                exc_info=True,
            )


def _log_drop(state: _FlusherState) -> None:
    """Say a report was thrown away. First drop, then every hundredth."""
    if state.dropped == 1 or state.dropped % _DROP_LOG_EVERY == 0:
        logger.warning(
            "call observations: queue full at %d items, dropped this report "
            "(%d dropped in this process). The endpoint sheds load rather than "
            "blocking the reporter, so the gap is in our data, not in the call.",
            state.queue.maxsize,
            state.dropped,
        )


# ---------------------------------------------------------------------------
# Process-level accessors (counters + shutdown)
# ---------------------------------------------------------------------------


def call_observation_stats() -> dict[str, int | bool]:
    """The observation queue's counters for this process.

    ``dropped_total`` is the drop counter the bounded queue increments; the same
    number is returned on every POST. ``failed_total`` counts reports that were
    accepted and then failed to write, which is a different loss from a drop and
    is counted separately on purpose.
    """
    state = _state
    if state is None:
        return {
            "queue_depth": 0,
            "dropped_total": 0,
            "written_total": 0,
            "failed_total": 0,
            "flusher_running": False,
        }
    return {
        "queue_depth": state.queue.qsize(),
        "dropped_total": state.dropped,
        "written_total": state.written,
        "failed_total": state.failed,
        "flusher_running": state.task is not None and not state.task.done(),
    }


async def shutdown_call_observations(
    *, drain: bool = True, timeout: float = _DRAIN_TIMEOUT_SECONDS
) -> None:
    """Stop the flusher, writing what is already queued first.

    Called from the app lifespan on shutdown. Every report already queued was
    answered with a 202, so a drain (bounded by ``timeout``, never unbounded --
    shutdown must not hang on a wedged database) is what keeps that 202 honest.
    Anything still queued when the timeout expires is lost, and logged as lost.

    Safe to call when nothing was ever enqueued, and safe to call twice.
    """
    global _state
    state = _state
    if state is None:
        return
    try:
        running: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover - no loop at all
        running = None
    if state.loop is not running:
        # A different loop owns that queue; touching it from here would raise.
        # Drop the reference and say what went with it.
        if state.queue.qsize():
            logger.warning(
                "call observations: %d queued report(s) belong to a loop that is "
                "gone; they are lost",
                state.queue.qsize(),
            )
        _state = None
        return

    task = state.task
    if drain and task is not None and not task.done():
        try:
            await asyncio.wait_for(state.queue.join(), timeout)
        except TimeoutError:
            logger.warning(
                "call observations: %d report(s) still queued after %.1fs of "
                "draining; they are lost",
                state.queue.qsize(),
                timeout,
            )
    if task is not None and not task.done():
        remaining = state.queue.qsize()
        if remaining:
            logger.warning(
                "call observations: stopping the flusher with %d report(s) "
                "queued; they are lost",
                remaining,
            )
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    _state = None


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("/observations", status_code=202)
async def post_call_observation(
    observation: CallObservation,
    request: Request,
    response: Response,
) -> dict[str, int | str]:
    """Accept one self-reported call observation. Returns without writing it.

    202 once the report is queued, 429 when the queue is full and the report was
    dropped, 503 when the path is switched off or this collector has no call
    storage. There is deliberately no ``await`` on the database in this
    function: the reporter is an agent in the middle of the job whose latency
    this data describes.
    """
    if _disabled():
        # Fully off: nothing is enqueued and no flusher is started. Reported as
        # 503 with the variable named, so an operator debugging "why is nothing
        # arriving" is told, rather than left with a silent 202.
        raise HTTPException(
            status_code=503,
            detail=f"call observations are disabled ({_KILL_SWITCH_ENV})",
        )

    storage = get_gateway(request).storage
    if storage is None:
        raise HTTPException(
            status_code=503,
            detail="call storage is disabled on this collector",
        )

    state = _state_for_running_loop()
    _ensure_flusher(state)
    item = _QueuedObservation(
        storage=storage,
        observation=observation,
        # Server-side, from the verified key. Absent for a static-key or
        # no-credential (single-tenant) deployment, which is the OSS default.
        tenant_id=getattr(request.state, "api_key_tenant_id", None),
    )
    try:
        state.queue.put_nowait(item)
    except asyncio.QueueFull:
        state.dropped += 1
        _log_drop(state)
        response.status_code = 429
        return {
            "status": "dropped",
            "queue_depth": state.queue.qsize(),
            "dropped_total": state.dropped,
        }

    return {
        "status": "queued",
        "queue_depth": state.queue.qsize(),
        "dropped_total": state.dropped,
    }


__all__ = [
    "CallObservation",
    "LegObservation",
    "call_observation_stats",
    "router",
    "shutdown_call_observations",
]
