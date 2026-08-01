"""Dashboard endpoint: GET /api/nodes -- what the boxes looked like per call.

The read side of T3 (``node_samples``, the livekit-server / livekit-sip /
node_exporter scrape) and C2 (``node_correlation_repository``, which joins a
time WINDOW to those samples). Both have existed with tests and neither had a
consumer, so the one number the whole layer exists to publish -- "the knee at 25
clients correlated with ``filefd_allocated`` reaching ``filefd_maximum`` on one
host" -- was unreadable from the product.

**Overlap is not attribution, and this endpoint cannot pretend otherwise.**
Layer 7 counters are node-wide; ``node_samples`` has no ``call_id`` by design.
A node listed against a call means *rows exist for that node inside this call's
padded window*, nothing more. That is why every window carries ``pad_ms`` and
both the requested and the padded bounds: "within 15 s of this call" is a weaker
claim than "during this call", and a reader who cannot see the pad cannot judge
which one they were handed.

Like ``dashboard/correlation.py`` this is a **pure passthrough**:

* **Nothing here computes.** The window, its padding, the status, the per-node
  counts, the gauge summaries and the counter rates are all decided in
  ``node_correlation_repository`` (whose rates come from the single
  counter-reset implementation in ``node_samples_repository.counter_rates``).
  This handler adds no field, no percentile and no verdict of its own.
* **A NULL is data.** Every ``None`` the repository produced is forwarded as
  ``null`` and none is defaulted to 0. The whole T3/C2 design stores NULL rather
  than 0 precisely so the last hop can tell an unmeasured series from a measured
  zero, and flattening one here would undo all of it.
* **Three statuses, and they are not interchangeable.**
  ``WINDOW_STATUSES`` is closed: ``correlated`` (a successful scrape overlaps
  the window), ``scrape_failed`` (rows exist in the window and every one of them
  failed, so the nodes were being watched and the watching did not work) and
  ``no_samples`` (nothing was scraped in the window at all). None of the three
  is a zero and none is the same claim as another.
* **Polled, never pushed.** The dashboard has zero WebSocket by design; the
  panel reads once per mount.

``samples_stored`` is the whole ``node_samples`` row count, and it is here for
the state an operator actually starts in: with no scrape target configured every
window is honestly ``no_samples``, and only the row count separates "this
deployment scrapes nothing" from "this particular window had nothing in it".

Auth is declared ONCE on the router, the way ``dashboard/calls.py`` and
``dashboard/correlation.py`` do it: a per-handler dependency is a thing a later
handler can forget. :func:`require_principal` is the dependency the sibling
dashboard *reads* use; ``require_scope(ADMIN_SCOPE)`` is reserved for the
routers that spend money or touch infra, and reading rows that are already on
disk does neither.

A non-admin (tenant-bound) principal is refused rather than served, the same
reasoning ``dashboard/correlation.py`` applies and for the same reason:
``node_samples`` has no tenant dimension at all -- a node is infrastructure and
serves every tenant at once -- so there is no scoped answer to give, and serving
the deployment-wide one to a tenant key would publish every other tenant's
infrastructure and (through the call rows it is keyed by) their call volume. The
self-hosted operator default (no credential, or a static config key) is an admin
principal and is unaffected.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from voicegateway.server.api._deps import (
    Principal,
    get_gateway,
    require_principal,
)

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

router = APIRouter(
    prefix="/nodes",
    tags=["dashboard"],
    dependencies=[Depends(require_principal)],
)

# How many recent calls are correlated. Each call costs one discovery query plus
# two reads per (node, source) target sampled in its window, so the cap bounds
# the query count and not just the payload: the budget is
# ``1 + limit * (1 + 2 * targets)``. Ten calls against a ten-node fleet scraped
# from three exporters is the worst case this endpoint may cost a SQLite writer
# that is also absorbing webhooks, which is why the cap is well below the 200 of
# ``/api/calls`` (whose per-call cost is one read, not one per target).
DEFAULT_LIMIT = 5
MAX_LIMIT = 10

# Widest pad a caller may ask for, on EACH side. An hour of padding around a
# four-second call would correlate it to an hour of unrelated fleet activity
# while still calling itself a correlation; the bound keeps the claim within
# arguing distance of the call. The default is the repository's
# ``DEFAULT_WINDOW_PAD_MS`` (one scrape interval) and is resolved there, not
# here, so there is one definition of it.
MAX_PAD_MS = 300_000


@router.get("")
async def get_node_correlation(
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    pad_ms: int | None = Query(None, ge=0, le=MAX_PAD_MS),
    gateway: Gateway = Depends(get_gateway),
    principal: Principal = Depends(require_principal),
) -> dict[str, Any]:
    """Return recent calls with the node samples overlapping each one's window.

    Shape: ``{"pad_ms": int, "samples_stored": int, "calls": [<calls row> +
    {"correlation": <WindowCorrelation>|null}]}``, where the correlation is
    ``node_correlation_repository.WindowCorrelation`` as that dataclass
    serialises (``window``, ``status``, ``nodes_sampled``). No field is renamed,
    added or dropped here.

    ``pad_ms`` is echoed at the top level as well as inside every window, so the
    pad is readable even by a deployment that has recorded no call yet. It is
    the parameter that makes the correlation auditable: everything under
    ``correlation`` describes the requested span widened by this much on each
    side, and it is never presented as part of the call.

    ``correlation: null`` means the CALL has no closed span -- still in flight,
    or an INVITE that never produced a room. It is not ``no_samples``: nothing
    was looked for, because substituting "now" for a missing end would invent the
    span and make the overlap set depend on when the page was loaded.

    503 when storage is disabled, rather than an empty correlation: "nothing was
    scraped" and "this deployment records nothing" are different facts, and the
    reader must not be shown the first when the second is true.
    """
    if gateway.storage is None:
        raise HTTPException(status_code=503, detail="Storage not configured")
    if not principal.is_admin:
        # See the module docstring: node samples have no tenant dimension, so
        # the only alternatives are refusing and leaking.
        raise HTTPException(
            status_code=403,
            detail="node samples are deployment-wide infrastructure and have no "
            "per-tenant answer",
        )
    return await gateway.storage.correlate_recent_calls_to_nodes(
        limit=limit, pad_ms=pad_ms
    )
