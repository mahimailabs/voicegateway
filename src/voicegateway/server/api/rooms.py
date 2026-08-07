"""GET /v1/rooms/{room}/latency: the per-stage split, over HTTP.

Why this endpoint exists
------------------------
The component split (turn detection / STT / LLM / TTS) has only ever been
readable in-process. ``voicegw livekit latency`` gets it by calling
:class:`~voicegateway.livekit_diag.latency.ComponentReader` against the SAME
local store the agent wrote to, and the CLI docs say so:

    This read-back is co-located in this version: the agent and the prober must
    share the same local VoiceGateway store. In collector mode
    (``VOICEGW_COLLECTOR_URL`` set) the rows go to the collector rather than this
    host, so the split is not shown from the CLI.

A deployment that runs the agent in one container and the collector in another
can therefore never see its own split, even though the collector holds every row
needed to compute it. Nothing here is new measurement: this is the existing
computation, reachable.

Keyed on the ROOM, not the session
----------------------------------
Deliberately. A caller mints the LiveKit room name when it signs the token, so
the room is the only identifier both sides hold at call time. ``session_id`` is
minted inside VoiceGateway and a caller never observes it, so an endpoint keyed
on it would be unusable by the process that needs it.

The room is also already the correlation key on the write side:
:func:`~voicegateway.repository.request_log_repository.get_requests_for_room`
exists for exactly this, because ``attach`` stamps ``metadata.room`` on every
row. Turn rows are keyed by ``session_id``, and this endpoint bridges the two
through the ``session_id`` those same request rows carry, rather than threading
room names down into the turn tracker.

Four claims this endpoint keeps distinct
----------------------------------------
The consumer renders a different state for each, so collapsing any pair would
lose information it needs:

1. **404** -- this collector has never seen that room.
2. **200 with ``components: null``** -- the room is known, and nothing
   correlated: an uninstrumented agent, or no completed turn yet.
3. **200 with ``complete: false``** -- a real split, still partial because the
   agent process is mid-flush. Poll again; do not render it. This mirrors
   :func:`~voicegateway.livekit_diag.latency._split_complete`, and it is the
   field that makes polling safe. It is the same reason ``ComponentReader``
   polls instead of returning its first read.
4. **200 with a null component** -- that stage produced no measurement. It is
   never ``0``. A stage rendered as ``0.00`` reads as "instant", and these
   numbers go into a document somebody reads months later, so a fabricated zero
   would ship as a false claim.

Milliseconds, everywhere
------------------------
``aggregate_components`` works in SECONDS (it averages raw provider ttfb values
that arrive that way) and its keys are unsuffixed. This endpoint converts once,
here, and suffixes every key ``_ms``, matching the rest of the HTTP API. The
diagnostics probe endpoint reports seconds and carries a warning about it; there
is no reason to make a second surface a reader has to remember a unit for.

Not built, on purpose
---------------------
No CORS and no browser-facing key: a read-scoped key in a browser can read every
session and cost row for the tenant, and the consumer proxies server-side
anyway. No WebSocket or SSE: ingest is batched and async behind a bounded queue,
so freshness is inherently seconds, and a socket would deliver seconds-stale
data faster while adding a stateful fan-out surface beside a deliberately simple
write path. No per-turn component split: the requirement is report-level, and
joining component rows onto individual turns is real work for no requirement.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from voicegateway.livekit_diag.latency import (
    LatencyResult,
    _split_complete,
    aggregate_components,
    summarize,
)
from voicegateway.repository import turns_repository as turns
from voicegateway.server.api._deps import (
    Principal,
    get_gateway,
    require_principal,
    resolve_read_tenant,
)

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

# Read-only, like the dashboard reads: ``require_principal`` rather than
# ``require_scope(ADMIN_SCOPE)``, which is reserved for routers that spend money
# or touch infra. Declared ONCE on the router, for the reason
# ``dashboard/sessions.py`` gives: a per-handler dependency is a thing the next
# handler in the file can forget.
router = APIRouter(
    prefix="/rooms",
    tags=["rooms"],
    dependencies=[Depends(require_principal)],
)

#: ``aggregate_components`` key -> the wire name. The seconds-to-milliseconds
#: conversion is applied to every one of them; see the module docstring.
#:
#: Written out rather than derived by suffixing, because two of the names are
#: not a suffix away: ``tts`` is a time-to-first-BYTE and says so on the wire,
#: and ``stt`` is the headline that ``aggregate_components`` picks from up to
#: three candidate sources. A reader of the JSON should not have to know that.
_COMPONENT_FIELDS: tuple[tuple[str, str], ...] = (
    ("eou", "eou_ms"),
    ("stt_ttfp", "stt_ttfp_ms"),
    ("stt_transcription_delay", "stt_transcription_delay_ms"),
    ("stt", "stt_ms"),
    ("llm_ttft", "llm_ttft_ms"),
    ("tts", "tts_ttfb_ms"),
)


def _not_found(room: str) -> HTTPException:
    """The single 404 an unknown OR unreadable room gets.

    A foreign-tenant room is the same 404 as a room that does not exist, for the
    reason ``require_visible_session`` gives: a 403 would confirm the room
    exists, and its existence is the fact being protected.
    """
    return HTTPException(status_code=404, detail=f"Room '{room}' not found")


def _visible(row: dict[str, Any], tenant: str | None) -> bool:
    """Whether one request row is readable by a principal scoped to ``tenant``.

    Same three cases as ``dashboard/sessions._visible``: ``None`` is the
    operator/admin (every row), ``""`` is the unattributed bucket
    (``tenant_id IS NULL``), anything else is an exact match.
    """
    if tenant is None:
        return True
    stored = row.get("tenant_id")
    if tenant == "":
        return stored is None
    return stored == tenant


def _components_ms(split: dict[str, Any]) -> dict[str, float | None]:
    """One split, converted to the wire shape. Seconds in, milliseconds out.

    Every field is present, null where the call produced nothing, so a consumer
    can render a fixed set of stages without inferring which keys to expect.
    Never ``0.0`` for a missing stage: see the module docstring.

    Takes the already-computed split rather than the rows, so
    ``aggregate_components`` runs exactly once per request. Running it twice
    (once for the values, once for ``complete``) would put two readings of the
    same rows in one response, and nothing would notice if they disagreed.
    """
    return {
        wire: (float(split[key]) * 1000.0 if key in split else None)
        for key, wire in _COMPONENT_FIELDS
    }


def _e2e_ms(samples: list[float]) -> dict[str, float | int] | None:
    """End-to-end response speed, summarised, or None when nothing completed.

    Computed by :func:`~voicegateway.livekit_diag.latency.summarize`, the same
    function the CLI reports through, so the two surfaces cannot drift on what
    p95 means. ``summarize`` reports ``trials``; the wire calls it ``n``.

    ``summarize`` returns zeros for an empty sample list, which is right for a
    CLI table and wrong here: a p95 of ``0.0`` on a call that answered nothing
    is a fabricated measurement. So the empty case returns None instead.
    """
    if not samples:
        return None
    stats = summarize(LatencyResult(agent="", e2e_samples=samples))
    return {
        "p50": stats["p50"],
        "p95": stats["p95"],
        "avg": stats["avg"],
        "min": stats["min"],
        "max": stats["max"],
        "n": stats["trials"],
    }


@router.get("/{room_name}/latency")
async def get_room_latency(
    room_name: str,
    since_turn: int | None = Query(
        None,
        description=(
            "Return only turns with turn_index greater than this. Filters the "
            "turns list ONLY: components and e2e_ms stay whole-call aggregates."
        ),
    ),
    gateway: Gateway = Depends(get_gateway),
    principal: Principal = Depends(require_principal),
) -> dict[str, Any]:
    """The per-stage latency split and per-turn timings for one LiveKit room.

    ``since_turn`` is a cursor over ``turns`` and nothing else. ``components``
    and ``e2e_ms`` are aggregates over the whole call and always come back
    whole: a cursor that quietly narrowed them would make each poll disagree
    with the one before it, which is exactly the bug a polling consumer cannot
    see.
    """
    if gateway.storage is None:
        raise HTTPException(status_code=503, detail="Storage not configured")

    rows = await gateway.storage.get_requests_for_room(room_name)
    if not rows:
        raise _not_found(room_name)

    # Tenancy is decided on the room's OWN rows, which already carry tenant_id,
    # rather than by looking up a parent session: the room may span several
    # sessions, and a row is the thing whose tenant is authoritative.
    tenant = resolve_read_tenant(principal, None)
    visible = [row for row in rows if _visible(row, tenant)]
    if not visible:
        raise _not_found(room_name)

    session_ids: list[str] = []
    for row in visible:
        sid = row.get("session_id")
        if isinstance(sid, str) and sid and sid not in session_ids:
            session_ids.append(sid)
    project = next(
        (row["project"] for row in visible if row.get("project")),
        None,
    )

    await gateway.storage._ensure_initialized()
    turn_rows: list[Any] = []
    async with gateway.storage._conn.session() as db:
        for sid in session_ids:
            turn_rows.extend(await turns.list_turns_by_session(db, sid))
    turn_rows.sort(key=lambda t: t.turn_index)

    # Computed ONCE; both `components` and `complete` are read off it.
    split = aggregate_components(visible)
    samples = [
        float(t.response_speed_ms) for t in turn_rows if t.response_speed_ms is not None
    ]
    shown = [t for t in turn_rows if since_turn is None or t.turn_index > since_turn]
    return {
        "room": room_name,
        "project": project,
        # The whole call, not the filtered view: a consumer polling with a
        # cursor still needs to know how many turns the aggregates cover.
        "turn_count": len(turn_rows),
        # False when nothing correlated at all, which is also the right answer
        # for a caller deciding whether to poll again.
        "complete": split is not None and _split_complete(split),
        "components": _components_ms(split) if split is not None else None,
        "e2e_ms": _e2e_ms(samples),
        "turns": [
            {
                "turn_index": t.turn_index,
                "caller_speak_end_ms": t.caller_speak_end_ms,
                "agent_speak_start_ms": t.agent_speak_start_ms,
                "response_speed_ms": t.response_speed_ms,
            }
            for t in shown
        ],
    }
