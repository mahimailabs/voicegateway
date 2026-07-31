"""Dashboard endpoint: GET /api/correlation -- how often sessions join calls.

The read side of ``session_repository.read_correlation_rate``, which has existed
(with a storage passthrough) since the sessions <-> calls join was written and
which nothing served until now. Without it the 90% warn threshold is a constant
no operator can read, and the join's defining property -- that it fails
SILENTLY, so a missing webhook receiver looks exactly like a healthy deployment
-- goes unreported.

Like ``dashboard/calls.py`` this is a **pure passthrough**:

* **Nothing here computes.** The rate, the counts, the threshold and the status
  are all decided in ``session_repository.read_correlation_rate``. This handler
  serialises the dataclass and adds no field, no percentage and no verdict of
  its own.
* **A NULL is data.** ``rate`` is ``None`` when ``eligible`` is 0, and it is
  forwarded as ``null``. It is not defaulted to 0.0 on the way out: "no session
  in this deployment ever had a room to join" is not "nothing correlates", and
  the second reads as an outage. ``status`` carries the same fact as
  ``"unknown"``, one of the closed set ``CORRELATION_STATUSES``.
* **Polled, never pushed.** The dashboard has zero WebSocket by design; the
  panel reads once per mount.

Auth is declared ONCE on the router, the way ``dashboard/calls.py`` does it: a
per-handler dependency is a thing a later handler can forget.
:func:`require_principal` is the dependency the sibling dashboard *reads* use
(``dashboard/calls.py``, ``dashboard/costs.py``, ``dashboard/sessions.py``);
``require_scope(ADMIN_SCOPE)`` is reserved for the routers that spend money or
touch infra, and reading a count that is already on disk does neither.

A non-admin (tenant-bound) principal is refused rather than served. The numbers
here are deployment-wide by construction: ``read_correlation_rate`` filters by
project and by time window, and has no tenant dimension at all, so there is no
scoped answer to give. Serving the global one to a tenant key would publish
every other tenant's session volume. The self-hosted operator default (no
credential, or a static config key) is an admin principal and is unaffected.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException

from voicegateway.server.api._deps import (
    Principal,
    get_gateway,
    require_principal,
)

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

router = APIRouter(
    prefix="/correlation",
    tags=["dashboard"],
    dependencies=[Depends(require_principal)],
)


@router.get("")
async def get_correlation_rate(
    gateway: Gateway = Depends(get_gateway),
    principal: Principal = Depends(require_principal),
) -> dict[str, Any]:
    """Return the sessions <-> calls correlation rate, exactly as computed.

    Shape: ``session_repository.CorrelationRate`` as that dataclass serialises --
    ``eligible``, ``correlated``, ``rate``, ``ambiguous``, ``dangling``,
    ``no_room``, ``warn_threshold``, ``status``. No field is renamed, added or
    dropped here, and ``warn_threshold`` travels with the number so the reader
    can see what the verdict was measured against instead of taking 90% on
    faith.

    ``rate`` is ``null`` when ``eligible`` is 0 and ``status`` is then
    ``"unknown"``. That pair is the "not measured" case and the UI must render
    it as such: a 0% here would report an unmeasured deployment as a broken one.

    503 when storage is disabled, rather than an unknown-looking payload:
    "nothing correlated yet" and "this deployment records nothing" are different
    facts, and the reader must not be shown the first when the second is true.
    """
    if gateway.storage is None:
        raise HTTPException(status_code=503, detail="Storage not configured")
    if not principal.is_admin:
        # See the module docstring: there is no tenant-scoped version of this
        # number, so the only alternatives are refusing and leaking.
        raise HTTPException(
            status_code=403,
            detail="the correlation rate is deployment-wide and has no "
            "per-tenant answer",
        )
    return await gateway.storage.read_correlation_rate()
