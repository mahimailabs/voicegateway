"""Dashboard endpoint: GET /api/calls -- the recorded call, with its legs.

The read side of the ``calls`` / ``call_legs`` tables, which the LiveKit webhook
receiver and agent self-reports write. It is what the Diagnostics page's layer
waterfall draws, and it is a **pure passthrough**:

* **Nothing here computes.** ``calls.answer_latency_ms`` is derived in exactly
  one place, ``calls_repository._derive_answer_latency``, at write time. This
  handler forwards the stored value and its ``answer_latency_source`` untouched.
  It also serves no percentile: a page of rows is not the population, and
  publishing a p95 off six cards would be a number nobody could reproduce.
* **A NULL is data.** No field is defaulted, coerced or backfilled on the way
  out. ``answer_latency_ms: null`` means "the recorded timestamps do not support
  a ring time" -- a call that never answered, the most important row in a load
  test -- and rendering that as ``0`` would report the worst call as the best
  one. The UI distinguishes the two; flattening them here would defeat it.
* **Polled, never pushed.** The dashboard has zero WebSocket by design and this
  surface is no exception: the panel reads once per mount.

Auth is declared ONCE, on the router, for the same reason
``server/api/call_observations.py`` does it: a per-handler dependency is a thing
a later handler can forget, and this payload carries room names, SIP participant
identities and the ``sip.*`` attributes (caller phone number, trunk, SIP call
id). :func:`require_principal` is the dependency the sibling dashboard *reads*
use (``dashboard/costs.py``, ``dashboard/sessions.py``); ``require_scope(
ADMIN_SCOPE)`` is reserved for the routers that spend money or touch infra
(``dashboard/diagnostics.py``, ``dashboard/server.py``), and reading rows that
are already on disk does neither.

The handler takes the resolved :class:`Principal` as a parameter as well; FastAPI
caches a dependency per request, so the key is verified once, not twice.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from voicegateway.server.api._deps import (
    Principal,
    get_gateway,
    require_principal,
    resolve_read_tenant,
)

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

router = APIRouter(
    prefix="/calls",
    tags=["dashboard"],
    dependencies=[Depends(require_principal)],
)

# Page size. Each call costs one extra read for its legs, so the cap bounds the
# query count as well as the payload: 200 calls is 201 statements, which is the
# most this endpoint may cost a SQLite writer that is also absorbing webhooks.
# The waterfall panel asks for 6.
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def _visible(row: dict[str, Any], tenant: str | None) -> bool:
    """Whether a call row is readable by a principal scoped to ``tenant``.

    ``None`` is the operator/admin case: every row. ``""`` is the unattributed
    bucket, i.e. ``tenant_id IS NULL``, the same convention the repositories use
    for their SQL tenant predicate (``session_repository.list_sessions``).
    """
    if tenant is None:
        return True
    stored: str | None = row["tenant_id"]
    if tenant == "":
        return stored is None
    return stored == tenant


@router.get("")
async def get_calls(
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    gateway: Gateway = Depends(get_gateway),
    principal: Principal = Depends(require_principal),
) -> dict[str, Any]:
    """Return recent calls, newest first, each with its ordered legs.

    Shape: ``{"calls": [<calls row> + {"legs": [<call_legs row>, ...]}]}``. The
    row fields are ``calls_repository.CallRow`` / ``CallLegRow`` as those
    dataclasses serialise -- no field is renamed, added or dropped here.

    Load-test traffic is excluded: the repository's ``is_probe=False`` default
    keeps synthetic calls out of the numbers a reader takes for production, and
    this endpoint does not expose a knob to mix them back in.

    Tenant scoping: a tenant-bound (non-admin) key sees only its own calls. The
    filter runs here rather than in the query, so such a page can hold fewer than
    ``limit`` rows -- fewer, never another tenant's. The self-hosted operator
    default (no credential, or a static config key) is an admin principal and is
    unaffected.

    503 when storage is disabled, rather than an empty list: "no call has been
    recorded" and "this deployment records nothing" are different facts and the
    reader must not be shown the first when the second is true.
    """
    if gateway.storage is None:
        raise HTTPException(status_code=503, detail="Storage not configured")
    tenant = resolve_read_tenant(principal, None)
    rows = await gateway.storage.list_calls(limit=limit)
    calls: list[dict[str, Any]] = []
    for row in rows:
        if not _visible(row, tenant):
            continue
        legs = await gateway.storage.list_call_legs(row["id"])
        calls.append({**row, "legs": legs})
    return {"calls": calls}
