"""POST /v1/ingest: receive batched request records from fleet agents.

A self-hosted collector exposes this endpoint; the library's
``RemoteCollectorSink`` POSTs batches here. Auth is an api-key bearer token
(``require_scope`` short-circuits on the ``vk_`` prefix and sets the tenant
ContextVar, which the request-log repository reads at write time -> the tenant
is stamped server-side, not trusted from the payload). ``id`` is the record's
UUID, so a duplicate (a sink retry) is counted, not double-written.
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
import math
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError

from voicegateway.middleware.dead_air_detector_middleware import DeadAirEvent
from voicegateway.middleware.turn_tracker_middleware import TurnRow
from voicegateway.models.request_model import RequestRecord
from voicegateway.server.api._deps import get_gateway, require_scope

logger = logging.getLogger(__name__)

router = APIRouter()

_RECORD_FIELDS: frozenset[str] = frozenset(
    f.name for f in dataclasses.fields(RequestRecord)
)

_TURN_FIELDS: frozenset[str] = frozenset(f.name for f in dataclasses.fields(TurnRow))

_DEAD_AIR_FIELDS: frozenset[str] = frozenset(
    f.name for f in dataclasses.fields(DeadAirEvent)
)


def _record_from_payload(raw: dict[str, Any]) -> RequestRecord | None:
    """Build a RequestRecord from a payload dict, ignoring unknown keys.

    Returns None for a malformed record (missing required fields) so one bad
    row in a batch does not reject the whole batch.

    Billing note: the caller re-rates each record against the collector's own
    rate card before persisting (see ``ingest``), so an agent-supplied
    ``rated_price_usd`` / ``rate_rule`` on the payload is overwritten. Agents
    rate at cost pass-through (no card client-side); the collector is the
    source of truth for margins. See docs/architecture/rating.md.
    """
    filtered = {k: v for k, v in raw.items() if k in _RECORD_FIELDS}
    try:
        return RequestRecord(**filtered)
    except TypeError:
        return None


def _rate_limit_key(request: Request) -> str:
    """Identity for ingest rate limiting: virtual key, then API-key hash, then IP.

    ``require_scope`` sets ``api_key_id`` only on the api-key path, so
    static-key and bare callers fall through to the API-key hash or client IP.
    """
    vk = getattr(request.state, "api_key_id", None)
    if vk is not None:
        return f"vk:{vk}"
    auth = request.headers.get("Authorization")
    if auth:
        return "ak:" + hashlib.sha256(auth.encode()).hexdigest()
    client = request.client
    return "ip:" + (client.host if client is not None else "unknown")


@router.post("/ingest")
async def ingest(
    records: list[dict[str, Any]],
    request: Request,
    _auth: None = Depends(require_scope("write")),
) -> dict[str, int]:
    """Persist a batch of request records pushed by a fleet agent."""
    gateway = get_gateway(request)
    storage = gateway.storage

    # Rate limit (429) takes precedence over the batch-size cap (413); both run
    # before any database write.
    limiter = getattr(request.app.state, "ingest_rate_limiter", None)
    if limiter is not None:
        retry_after = limiter.check(_rate_limit_key(request))
        if retry_after is not None:
            raise HTTPException(
                status_code=429,
                detail="ingest rate limit exceeded",
                headers={"Retry-After": str(math.ceil(retry_after))},
            )

    max_batch = gateway.config.ingest.max_batch_size
    if len(records) > max_batch:
        raise HTTPException(
            status_code=413,
            detail=f"batch too large: {len(records)} records exceeds max {max_batch}",
        )

    ch_client = getattr(request.app.state, "ch_client", None)

    if ch_client is not None:
        # ClickHouse path: fresh sink per request (fresh buffer, shared client).
        from voicegateway.services.sinks import ClickHouseSink

        sink = ClickHouseSink(ch_client)
        accepted = 0
        rejected = 0
        for raw in records:
            record = _record_from_payload(raw)
            if record is None:
                rejected += 1
                continue
            # Re-rate against the collector's card before persisting. Both the
            # SQLite and ClickHouse sinks store rated_price_usd / rate_rule.
            gateway.cost_tracker.rate_record(record)
            await sink.log_request(record)
            accepted += 1
        if rejected:
            logger.warning("ingest: skipped %d malformed record(s)", rejected)
        try:
            await sink.flush()
        except Exception as exc:  # noqa: BLE001
            # Signal the client to retry the whole batch. The deterministic
            # insert_deduplication_token makes the re-POST idempotent (a no-op
            # if the rows already landed), so a 503 gives lossless at-least-once
            # delivery without risking double-counts. We do NOT fall back to
            # SQLite here: ClickHouse is the telemetry store of record when
            # configured, and a silent SQLite write would split the data.
            logger.warning(
                "ingest: ClickHouse flush failed for %d record(s); returning 503 "
                "for client retry",
                accepted,
                exc_info=True,
            )
            raise HTTPException(
                status_code=503,
                detail="telemetry store temporarily unavailable",
            ) from exc
        # Dedup is handled server-side by async_insert_deduplicate; return 0.
        return {"accepted": accepted, "duplicates": 0}

    # SQLite path (default when no ClickHouse host is configured).
    if storage is None:
        raise HTTPException(
            status_code=503,
            detail="cost tracking storage is disabled on this collector",
        )
    accepted = 0
    duplicates = 0
    rejected = 0
    for raw in records:
        record = _record_from_payload(raw)
        if record is None:
            rejected += 1
            continue
        gateway.cost_tracker.rate_record(record)
        try:
            await storage.log_request(record)
            accepted += 1
        except IntegrityError:
            # Duplicate id: a sink retry re-sent an already-stored row.
            duplicates += 1
        except Exception:  # noqa: BLE001 - one bad record must not 500 the batch
            rejected += 1
            logger.warning(
                "ingest: failed to persist record %r", raw.get("id"), exc_info=True
            )

    if rejected:
        logger.warning("ingest: skipped %d malformed record(s)", rejected)
    return {"accepted": accepted, "duplicates": duplicates}


def _turn_from_payload(raw: dict[str, Any]) -> TurnRow | None:
    """Build a TurnRow from a payload dict, ignoring unknown keys.

    Returns None for a malformed row so one bad turn does not reject the batch,
    matching ``_record_from_payload``.
    """
    filtered = {k: v for k, v in raw.items() if k in _TURN_FIELDS}
    try:
        return TurnRow(**filtered)
    except TypeError:
        return None


@router.post("/ingest/turns")
async def ingest_turns(
    rows: list[dict[str, Any]],
    request: Request,
    _auth: None = Depends(require_scope("write")),
) -> dict[str, int]:
    """Persist a batch of conversation turns pushed by a fleet agent.

    Its own route rather than a discriminated record on ``/v1/ingest``. That
    handler builds a RequestRecord out of every dict it receives and counts
    anything that fails to build as malformed, so a turn row posted there would
    be answered ``200`` and silently dropped, which is the exact failure this
    endpoint exists to end.

    Always SQL, even when ClickHouse is configured for requests: ClickHouse has
    no turns table, and every reader of turns (``/v1/rooms/{room}/latency``,
    ``/api/sessions/{id}/turns``, the session aggregates) reads them from SQL.
    Splitting the write would make those readers empty again.
    """
    gateway = get_gateway(request)
    storage = gateway.storage
    if storage is None:
        raise HTTPException(
            status_code=503,
            detail="cost tracking storage is disabled on this collector",
        )

    max_batch = gateway.config.ingest.max_batch_size
    if len(rows) > max_batch:
        raise HTTPException(
            status_code=413,
            detail=f"batch too large: {len(rows)} turns exceeds max {max_batch}",
        )

    turns: list[TurnRow] = []
    rejected = 0
    for raw in rows:
        turn = _turn_from_payload(raw)
        if turn is None:
            rejected += 1
            continue
        turns.append(turn)

    accepted = 0
    if turns:
        try:
            accepted = await storage.log_turns(turns)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ingest/turns: failed to persist %d turn(s)", len(turns), exc_info=True
            )
            raise HTTPException(
                status_code=503,
                detail="telemetry store temporarily unavailable",
            ) from exc

    if rejected:
        logger.warning("ingest/turns: skipped %d malformed turn(s)", rejected)
    return {"accepted": accepted}


def _dead_air_from_payload(raw: dict[str, Any]) -> DeadAirEvent | None:
    """Build a DeadAirEvent from a payload dict, ignoring unknown keys."""
    filtered = {k: v for k, v in raw.items() if k in _DEAD_AIR_FIELDS}
    try:
        return DeadAirEvent(**filtered)
    except TypeError:
        return None


@router.post("/ingest/dead-air")
async def ingest_dead_air(
    events: list[dict[str, Any]],
    request: Request,
    _auth: None = Depends(require_scope("write")),
) -> dict[str, int]:
    """Persist observed dead-air events pushed by a fleet agent.

    Its own route for the same reason as ``/ingest/turns``: the request handler
    builds a RequestRecord out of every dict it receives, so anything else sent
    there is answered ``200`` and dropped.

    Always SQL, even under ClickHouse, because
    ``GET /api/sessions/{id}/dead_air`` reads them from SQL.
    """
    gateway = get_gateway(request)
    storage = gateway.storage
    if storage is None:
        raise HTTPException(
            status_code=503,
            detail="cost tracking storage is disabled on this collector",
        )

    max_batch = gateway.config.ingest.max_batch_size
    if len(events) > max_batch:
        raise HTTPException(
            status_code=413,
            detail=f"batch too large: {len(events)} events exceeds max {max_batch}",
        )

    parsed: list[DeadAirEvent] = []
    rejected = 0
    for raw in events:
        event = _dead_air_from_payload(raw)
        if event is None:
            rejected += 1
            continue
        parsed.append(event)

    accepted = 0
    if parsed:
        try:
            accepted = await storage.log_dead_air(parsed)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ingest/dead-air: failed to persist %d event(s)",
                len(parsed),
                exc_info=True,
            )
            raise HTTPException(
                status_code=503,
                detail="telemetry store temporarily unavailable",
            ) from exc

    if rejected:
        logger.warning("ingest/dead-air: skipped %d malformed event(s)", rejected)
    return {"accepted": accepted}


__all__ = ["router"]
