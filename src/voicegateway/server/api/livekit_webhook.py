"""POST /v1/livekit/webhook: LiveKit server webhooks -> ``calls`` + ``call_legs``.

This is how a call that runs **zero inference** gets a row: LiveKit posts room,
participant and track lifecycle events here, and each one is merged into the
call it belongs to through :mod:`voicegateway.repository.calls_repository`.

Security, which is the whole story for this endpoint
---------------------------------------------------
LiveKit posts from its own network, so this path is reachable from the public
internet, and **the webhook signature is its entire authentication boundary**.
Two invariants hold that boundary:

* **Verify before parse.** The raw bytes go from ``await request.body()``
  straight into :func:`_verified_event`, and :class:`livekit.api.WebhookReceiver`
  checks the JWT (HS256, ``iss`` = the LiveKit API key) *and* the body's sha256
  against the signed claim before ``json_format.Parse`` ever runs. Nothing in
  this module reads, decodes, logs or persists a field from an unverified body.
  :func:`_verified_event` is the only function here that returns a
  ``WebhookEvent``, so there is no way to obtain a parsed event without
  verifying it first.
* **No missing-credentials bypass.** If the API key/secret are not configured --
  or the optional ``livekit`` extra is not installed -- the request is
  **rejected**. There is deliberately no "no creds, so skip verification"
  branch: that shortcut turns one misconfigured deploy into an open,
  unauthenticated write endpoint for anyone on the internet.

The guard runs inside the handler body rather than as a router dependency, so a
future endpoint added to this router cannot forget to opt in: the verified event
is the only input the handler has.

This is deliberately **not** part of ``/v1/ingest``. That endpoint carries
``RequestRecord`` rows and re-rates them against the cost card, so pushing
call-level SIP records through it would corrupt the cost model.

What is handled
---------------
Only events LiveKit actually sends (verified against ``livekit-protocol``):

===============================  ==============================================
``room_started``                 call row; ``started_at_ms`` from the room's
                                 creation time
``room_finished``                ``ended_at_ms`` (the Room message carries no
                                 end time, so the event's own timestamp is it)
``participant_joined``           leg row: identity/kind/region/publisher +
                                 ``sip.*`` attributes, ``joined_at_ms``
``participant_left``             leg ``left_at_ms`` + ``disconnect_reason``
``participant_connection_aborted``  same, for a leg that never finished joining
                                 (this is where ``SIP_TRUNK_FAILURE`` shows up)
``track_published``              audio only: ``first_audio_track_at_ms``,
                                 ``audio_track_sid``, ``audio_codec``
``track_unpublished``            call row only -- no column describes it
``egress_*`` / ``ingress_*``     accepted, not stored (no columns in M1)
===============================  ==============================================

There is no ``track_subscribed`` event and no ``participant_attributes_changed``
event; nothing here pretends otherwise. Per-call SIP response codes are not
fetchable either (``livekit-api`` exposes no ``ListSIPCallInfo``), so the
disconnect reason is the layer-1 failure cause we get.

Redelivery and ordering are handled by the repository, not here: every write is
an idempotent select-then-update merge that creates the row if it is missing, so
a retried POST is a no-op and ``participant_left`` arriving first still works.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request

from voicegateway.livekit_diag.config import CredsError, resolve_creds
from voicegateway.server.api._deps import get_gateway

if TYPE_CHECKING:  # pragma: no cover - typing only
    from voicegateway.services.storage_service import StorageService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/livekit", tags=["livekit"])

# Comma-separated SIP trunk ids whose traffic is synthetic load. D0 decision 1:
# ``is_probe`` is discriminated by a dedicated load-test trunk, NEVER by a
# caller-supplied header or run id -- anything on the wire can be forged, and a
# forged probe flag would hide real traffic from production percentiles.
_LOADTEST_TRUNK_ENV = "VOICEGW_LOADTEST_TRUNK_IDS"

# Placeholder passed to resolve_creds as the LiveKit url. Verifying a webhook
# signature needs the api key and secret only, and an explicit argument
# short-circuits url resolution there, so a deployment that never set
# LIVEKIT_URL can still verify webhooks. The value is never used for anything.
_URL_NOT_NEEDED = "webhook-verification-needs-no-url"

_ROOM_EVENTS = frozenset({"room_started", "room_finished"})
_PARTICIPANT_EVENTS = frozenset(
    {"participant_joined", "participant_left", "participant_connection_aborted"}
)
_TRACK_EVENTS = frozenset({"track_published", "track_unpublished"})

# The reason enum's zero value. Proto3 cannot distinguish "unset" from the
# default, so recording it would publish "UNKNOWN_REASON" as if we had observed
# a cause. It reads as None: this event did not say why.
_UNKNOWN_DISCONNECT_REASON = 0


# ---------------------------------------------------------------------------
# Credentials + verification
# ---------------------------------------------------------------------------


def _resolve_webhook_creds() -> tuple[str, str] | None:
    """``(api_key, api_secret)``, or ``None`` when either one is missing.

    Resolved through :func:`voicegateway.livekit_diag.config.resolve_creds`, the
    one credential precedence in this repo (``LIVEKIT_*`` env first, then the
    ``livekit:`` block in ``voicegw.yaml``), reused rather than reimplemented so
    the webhook receiver reads its credentials from exactly where the
    Diagnostics and Server pages read theirs.

    Returning ``None`` is what the caller turns into a rejection. This function
    never invents, defaults, or guesses a credential.
    """
    try:
        creds = resolve_creds(_URL_NOT_NEEDED, None, None)
    except CredsError as exc:
        logger.warning("livekit webhook rejected: %s", exc)
        return None
    return creds.api_key, creds.api_secret


def _verified_event(request: Request, body: bytes) -> Any:
    """Return the verified ``WebhookEvent`` for ``body``, or raise.

    The only function in this module that produces a parsed event, and it cannot
    produce one without a valid signature over exactly these bytes.

    Rejections are deliberately terse: the response never echoes the body, the
    token, or which check failed, so the endpoint is not a verification oracle.
    """
    creds = _resolve_webhook_creds()
    if creds is None:
        # FAIL CLOSED. An unconfigured deployment rejects every webhook rather
        # than accepting unauthenticated writes from the internet. Do not add a
        # "no creds, so skip verification" branch here. _resolve_webhook_creds
        # already logged which credential is missing.
        raise HTTPException(
            status_code=503,
            detail="LiveKit webhook verification is not configured",
        )

    try:
        from google.protobuf.json_format import ParseError
        from livekit.api import TokenVerifier, WebhookReceiver
    except ImportError as exc:  # pragma: no cover - depends on install extras
        # Same fail-closed rule for a missing optional dependency: without the
        # verifier there is no way to authenticate the caller.
        logger.warning(
            "livekit webhook rejected: the 'livekit' extra is not installed, so "
            "the signature cannot be verified"
        )
        raise HTTPException(
            status_code=503,
            detail="LiveKit webhook verification is not available",
        ) from exc

    # LiveKit sends the signed JWT as the bare Authorization header value; a
    # "Bearer " prefix is tolerated because proxies add one. An absent or empty
    # header is not special-cased: it simply fails verification below.
    token = (request.headers.get("Authorization") or "").strip()
    if token[:7].lower() == "bearer ":
        token = token[7:].strip()

    try:
        # Strict decode: the receiver re-encodes this text to check the body
        # hash, so a lossy decode could only ever cause a false rejection, never
        # let a mismatched body through.
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=401, detail="invalid webhook") from None

    receiver = WebhookReceiver(TokenVerifier(creds[0], creds[1]))
    try:
        return receiver.receive(text, token)
    except ParseError as exc:
        # Only reachable once the JWT signature AND the body sha256 have both
        # matched, i.e. from a genuinely authenticated sender whose payload this
        # protocol version cannot parse. Worth distinguishing from a 401.
        logger.warning("livekit webhook: verified body failed to parse: %s", exc)
        raise HTTPException(
            status_code=400, detail="unparseable webhook payload"
        ) from None
    except Exception:  # noqa: BLE001 - any failure is a rejection
        # Bad signature, wrong issuer, expired token, or a body that does not
        # match the signed hash. One status, one message, no detail.
        logger.warning(
            "livekit webhook rejected: signature verification failed",
            exc_info=True,
        )
        raise HTTPException(status_code=401, detail="invalid webhook") from None


# ---------------------------------------------------------------------------
# Field extraction (every helper treats proto's unset-0 as "not observed")
# ---------------------------------------------------------------------------


def _ms_from_seconds(seconds: int | float | None) -> int | None:
    """Epoch seconds -> epoch ms. ``0``/unset is not a timestamp, it is unknown."""
    if not seconds:
        return None
    return int(seconds * 1000)


def _ms(value_ms: int | None, value_seconds: int | float | None) -> int | None:
    """Prefer the millisecond field, fall back to the second one, else None."""
    if value_ms:
        return int(value_ms)
    return _ms_from_seconds(value_seconds)


def _enum_name(enum_wrapper: Any, number: int) -> str | None:
    """Enum number -> its proto name, or None if the number is not in the enum."""
    try:
        return str(enum_wrapper.Name(number))
    except ValueError:  # pragma: no cover - a newer server sending a new value
        return None


def _kind_name(participant: Any) -> str | None:
    """``'SIP'`` | ``'AGENT'`` | ``'STANDARD'`` | ... as LiveKit reports it."""
    from livekit.protocol.models import ParticipantInfo

    return _enum_name(ParticipantInfo.Kind, participant.kind)


def _disconnect_reason(participant: Any) -> str | None:
    """The leg's disconnect reason, or None when the event did not state one.

    ``SIP_TRUNK_FAILURE`` / ``USER_UNAVAILABLE`` / ``USER_REJECTED`` arrive here:
    they are the real layer-1 failure causes, and the reason this column exists.
    """
    from livekit.protocol.models import DisconnectReason

    reason = participant.disconnect_reason
    if reason == _UNKNOWN_DISCONNECT_REASON:
        return None
    return _enum_name(DisconnectReason, reason)


def _attributes(participant: Any) -> dict[str, str] | None:
    """The participant's attributes as a plain dict, or None when empty.

    The repository keeps only the ``sip.*`` keys (callID, callStatus,
    phoneNumber, trunkID, ruleID); everything else is application state that
    this table has no business copying.
    """
    attributes = dict(participant.attributes or {})
    return attributes or None


def _is_audio_track(track: Any) -> bool:
    """True for an audio track.

    ``TrackType.AUDIO`` is ``0``, which proto3 cannot tell from unset, so a
    populated ``mime_type`` is trusted first and the enum is only the fallback.
    """
    from livekit.protocol.models import TrackType

    mime = str(track.mime_type or "").lower()
    if mime:
        return mime.startswith("audio/")
    return bool(track.type == TrackType.AUDIO)


def _loadtest_trunk_ids() -> frozenset[str]:
    """SIP trunk ids configured as synthetic load, from the environment.

    Comma-separated in ``VOICEGW_LOADTEST_TRUNK_IDS``. Empty by default, and an
    empty set means no webhook ever sets ``is_probe`` -- which is the honest
    state, because a deployment with no dedicated load-test trunk genuinely
    cannot tell synthetic traffic from production traffic. Env only: the runtime
    ``GatewayConfig`` has no ``livekit`` block (only the validation schema does),
    and the typed home for this belongs with the load-run config, not here.
    """
    raw = os.environ.get(_LOADTEST_TRUNK_ENV) or ""
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def _probe_flag(attributes: dict[str, str] | None, trunk_ids: frozenset[str]) -> bool | None:
    """``True`` when this leg arrived on a configured load-test trunk, else None.

    Never ``False``: a load generator may have already flagged the call before
    any webhook arrived, and ``None`` ("this event did not say") is what stops an
    event that cannot see the trunk id from clearing that flag.
    """
    if not attributes or not trunk_ids:
        return None
    trunk = attributes.get("sip.trunkID")
    if trunk and trunk in trunk_ids:
        return True
    return None


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------


async def _store_event(
    storage: StorageService, event: Any, trunk_ids: frozenset[str]
) -> bool:
    """Merge one verified event into ``calls`` (+ ``call_legs``). True if stored.

    One ``upsert_call`` per event carrying a room, plus at most one
    ``upsert_call_leg``. Every value passed is one this event actually observed;
    anything it did not observe is ``None``, which the repository treats as "did
    not say" rather than "set back to unknown".
    """
    name = str(event.event or "")
    if name not in _ROOM_EVENTS | _PARTICIPANT_EVENTS | _TRACK_EVENTS:
        # egress_*, ingress_*, and anything a newer server adds. No column in
        # this schema describes them, and inventing one here would be worse.
        logger.debug("livekit webhook: no M1 mapping for event %r", name)
        return False

    room = event.room
    room_sid = str(room.sid or "") or None
    if room_sid is None:
        # room_sid is the only key this receiver can produce, and the repository
        # refuses a keyless event because it would create a new row per
        # redelivery. Accept the POST (retrying will not help) and say so.
        logger.warning("livekit webhook: event %r carried no room sid", name)
        return False

    # protobuf hands back a default-empty ParticipantInfo for an unset field, so
    # the sid (never NULL, half the leg's unique key) is what says there is one.
    participant = event.participant
    has_participant = bool(participant.sid)
    kind = _kind_name(participant) if has_participant else None
    attributes = _attributes(participant) if has_participant else None
    # Every event's Room snapshot carries the creation time, so start converges
    # from any event; the repository keeps the earliest value it is ever given.
    started_at_ms = _ms(room.creation_time_ms, room.creation_time)
    event_ms = _ms_from_seconds(event.created_at)
    if name == "room_started" and started_at_ms is None:
        started_at_ms = event_ms

    # The Room message has no end time, so room_finished's own timestamp is the
    # only observation of when the call ended. Deliberately not set from
    # participant_left: one leg leaving is a leg ending (call_legs.left_at_ms),
    # not the call ending, and deriving duration_ms from it would publish a
    # short duration for every multi-leg call.
    ended_at_ms = event_ms if name == "room_finished" else None

    # The call's end reason comes only from a terminating SIP leg: that leg is
    # the caller, so its disconnect reason is why the call ended as the caller
    # experienced it. An agent leg's reason stays on its own leg row instead of
    # overwriting it.
    end_reason = (
        _disconnect_reason(participant)
        if kind == "SIP"
        and name in {"participant_left", "participant_connection_aborted"}
        else None
    )
    # Only ever "sip", never "web": a late STANDARD join would otherwise clobber
    # the channel of a call we already knew had a SIP leg.
    channel = "sip" if kind == "SIP" else None

    call_id = await storage.upsert_call(
        origin="webhook",
        room_sid=room_sid,
        room_name=str(room.name or "") or None,
        started_at_ms=started_at_ms,
        ended_at_ms=ended_at_ms,
        end_reason=end_reason,
        channel=channel,
        is_probe=_probe_flag(attributes, trunk_ids),
    )

    if not has_participant:
        # room_started / room_finished, or a participant snapshot with no sid
        # (participant_sid is half the leg's unique key and must not be NULL).
        return True

    joined_at_ms: int | None = None
    left_at_ms: int | None = None
    is_publisher: bool | None = None
    disconnect_reason: str | None = None
    first_audio_track_at_ms: int | None = None
    audio_track_sid: str | None = None
    audio_codec: str | None = None

    if name in _PARTICIPANT_EVENTS:
        joined_at_ms = _ms(participant.joined_at_ms, participant.joined_at)
        is_publisher = bool(participant.is_publisher)
        if name != "participant_joined":
            # ParticipantInfo carries no leave time, so the event's timestamp is
            # the observation. participant_connection_aborted is included on
            # purpose: a leg that never finished joining still ended.
            left_at_ms = event_ms
            disconnect_reason = _disconnect_reason(participant)
    elif name == "track_published":
        # As with the participant, an unset track field comes back as a default
        # TrackInfo whose type reads as AUDIO (enum 0), so the sid is what says a
        # track was really reported. Without it we would stamp
        # first_audio_track_at_ms for a track that does not exist.
        track = event.track
        if track.sid and _is_audio_track(track):
            # For an agent leg this gates the caller's ring time: livekit-sip
            # withholds 200 OK until it subscribes to an audio track.
            first_audio_track_at_ms = event_ms
            audio_track_sid = str(track.sid or "") or None
            audio_codec = str(track.mime_type or "") or None

    await storage.upsert_call_leg(
        call_id=call_id,
        participant_sid=str(participant.sid),
        identity=str(participant.identity or "") or None,
        kind=kind,
        region=str(participant.region or "") or None,
        joined_at_ms=joined_at_ms,
        left_at_ms=left_at_ms,
        disconnect_reason=disconnect_reason,
        is_publisher=is_publisher,
        attributes=attributes,
        first_audio_track_at_ms=first_audio_track_at_ms,
        audio_track_sid=audio_track_sid,
        audio_codec=audio_codec,
    )
    return True


@router.post("/webhook")
async def livekit_webhook(request: Request) -> dict[str, str]:
    """Receive one signed LiveKit webhook and merge it into ``calls``.

    Returns 200 once the event is durably written (LiveKit retries on anything
    else, and every write here is idempotent, so a retry is safe).
    """
    gateway = get_gateway(request)

    # Raw bytes. Nothing below reads a field until _verified_event has checked
    # the signature and the body hash over exactly these bytes.
    body = await request.body()
    event = _verified_event(request, body)

    storage = gateway.storage
    if storage is None:
        raise HTTPException(
            status_code=503,
            detail="call storage is disabled on this collector",
        )

    dropped = int(getattr(event, "num_dropped", 0) or 0)
    if dropped:
        # LiveKit is telling us it gave up on earlier events. Say it out loud:
        # the gap is in our data, not in the call.
        logger.warning(
            "livekit webhook: LiveKit reported %d dropped event(s) before this one",
            dropped,
        )

    try:
        stored = await _store_event(storage, event, _loadtest_trunk_ids())
    except Exception as exc:  # noqa: BLE001 - a 503 asks LiveKit to retry
        logger.warning(
            "livekit webhook: failed to persist event %r", event.event, exc_info=True
        )
        raise HTTPException(
            status_code=503, detail="call store temporarily unavailable"
        ) from exc

    return {"status": "ok" if stored else "ignored"}


__all__ = ["router"]
