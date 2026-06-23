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

from voicegateway.models.request_model import RequestRecord
from voicegateway.server.api._deps import get_gateway, require_scope

logger = logging.getLogger(__name__)

router = APIRouter()

_RECORD_FIELDS: frozenset[str] = frozenset(
    f.name for f in dataclasses.fields(RequestRecord)
)


def _record_from_payload(raw: dict[str, Any]) -> RequestRecord | None:
    """Build a RequestRecord from a payload dict, ignoring unknown keys.

    Returns None for a malformed record (missing required fields) so one bad
    row in a batch does not reject the whole batch.
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
    if storage is None:
        raise HTTPException(
            status_code=503,
            detail="cost tracking storage is disabled on this collector",
        )

    # Rate limit (429) takes precedence over the batch-size cap (413); both run
    # before any database write. Storage-disabled (503 above) wins over both.
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

    accepted = 0
    duplicates = 0
    rejected = 0
    for raw in records:
        record = _record_from_payload(raw)
        if record is None:
            rejected += 1
            continue
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


__all__ = ["router"]
