"""Dashboard endpoint: GET /api/nodes/live, what the fleet is doing right now.

The sibling of ``dashboard/nodes.py`` and deliberately not the same question.
``GET /api/nodes`` is per-CALL correlation: keyed by recent calls, capped at ten
of them, read once per mount. It answers "what did the boxes look like during
this call". Neither it nor ``dashboard/loadtest.py`` (which lists IMPORTED runs,
and a run is imported after it has finished) can answer "what is the fleet doing
while this campaign is in flight", which is what a load-test runner needs to
show a fleet live.

**This adds no collection.** The rows are already on disk: the collector scrapes
every target on its own interval and writes ``node_samples``. This is a read of
what is already there. VoiceGateway profiles; it does not gain the ability to
run anything, and this endpoint must not become the place that changes.

The design rules are ``dashboard/nodes.py``'s, for the same reasons:

* **A NULL is data.** Every ``None`` the repository produced is forwarded as
  ``null`` and none is defaulted to 0. The whole T3 design stores NULL rather
  than 0 so the last hop can tell an unmeasured series from a measured zero, and
  a live view is exactly where flattening it would do the most harm: a node
  whose exporter stopped answering would render as a node carrying no traffic.
* **Pure passthrough.** Nothing here computes. Which row is newest, how old it
  is and whether that makes it stale are all decided in
  ``node_samples_repository.latest_per_target``. This handler adds no field and
  no verdict of its own.
* **A stale target is not a healthy zero.** The newest sample for a dead target
  is still a sample, with real numbers in it and no field that says they stopped
  being true. Every entry therefore carries ``age_seconds`` and ``stale``, and
  the response carries the ``stale_after_seconds`` it judged against, so a
  reader can see the threshold rather than infer it. Without that, a crashed
  node renders identically to a healthy one and gets more convincing the longer
  it stays down.
* **Polled, never pushed.** The dashboard has zero WebSocket by design. A caller
  that wants a live view polls this, which is why the freshness fields are part
  of the payload rather than something the transport implies.

Auth is declared ONCE on the router, as ``dashboard/nodes.py`` does it: a
per-handler dependency is a thing a later handler can forget.
:func:`require_principal` is the dependency the sibling dashboard reads use.

A tenant-bound principal is refused rather than served, for the reason
``dashboard/nodes.py`` gives: ``node_samples`` has no tenant dimension at all,
because a node is infrastructure and serves every tenant at once. There is no
scoped answer to give, and serving the deployment-wide one to a tenant key would
publish every other tenant's infrastructure. The self-hosted operator default
(no credential, or a static config key) is an admin principal and is unaffected.
"""

from __future__ import annotations

import time
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

# When a target's newest sample is old enough to stop calling it current.
#
# One scrape interval, not two: at one interval the next scrape was already due
# and did not land, which is the earliest moment the data stops describing now.
# A sample may legitimately be almost a full interval old, so this is the first
# threshold that separates "waiting for the next tick" from "the tick did not
# come".
#
# Restated here rather than imported from the scrape worker, which owns it as a
# private default: the read side should not reach into the worker's internals,
# and a guard test asserts the two stay equal so restating cannot drift into
# disagreeing.
DEFAULT_STALE_AFTER_SECONDS = 15.0

# The widest a caller may loosen the freshness bar. Past this the word "live"
# stops meaning anything: a five minute old reading of a fleet under a load
# campaign describes a fleet that no longer exists.
MAX_STALE_AFTER_SECONDS = 300.0


@router.get("/live")
async def get_live_nodes(
    stale_after_seconds: float = Query(
        DEFAULT_STALE_AFTER_SECONDS, gt=0, le=MAX_STALE_AFTER_SECONDS
    ),
    gateway: Gateway = Depends(get_gateway),
    principal: Principal = Depends(require_principal),
) -> dict[str, Any]:
    """Return the newest ``node_samples`` row per ``(node, source)``.

    Shape: ``{"stale_after_seconds": float, "samples_stored": int, "nodes":
    [<node_samples row> + {"age_seconds": float, "stale": bool}]}``. The stored
    columns keep their own names and values, so a NULL column arrives as
    ``null``.

    One entry per target, never a window. ``source`` is part of the key because
    the same node is usually scraped twice per tick (once as ``livekit-server``
    and once as ``node-exporter``), and collapsing them would drop half the
    series or silently prefer one exporter's view of the box.

    ``stale`` is not a health verdict about the node. It says only that nothing
    has been written for this target for longer than ``stale_after_seconds``,
    which can equally mean the node died, the exporter stopped answering, or the
    collector is not running. The endpoint cannot tell those apart and does not
    pretend to; ``outcome`` on the row itself is what says why a scrape failed
    when one did happen.

    An empty ``nodes`` list with a non-zero ``samples_stored`` means every
    target has aged out of the table entirely; with ``samples_stored`` at 0 it
    means nothing has ever been scraped here.

    503 when storage is disabled, rather than an empty list: "nothing is
    scraped" and "this deployment records nothing" are different facts, and the
    reader must not be shown the first when the second is true.
    """
    if gateway.storage is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "storage is not configured, so no node sample could have been "
                "recorded. This is not an idle fleet."
            ),
        )
    if not principal.is_admin:
        # See the module docstring: node samples have no tenant dimension, so
        # the only alternatives are refusing and leaking.
        raise HTTPException(
            status_code=403,
            detail="node samples are deployment-wide infrastructure and have no "
            "per-tenant answer",
        )
    return await gateway.storage.latest_node_samples(
        now_ms=int(time.time() * 1000),
        stale_after_ms=int(stale_after_seconds * 1000),
    )
