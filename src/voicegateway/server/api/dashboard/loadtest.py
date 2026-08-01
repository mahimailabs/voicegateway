"""``GET /api/loadtest/runs``: imported load runs and their per-test rows.

A READ endpoint, so it lives on ``dashboard_router`` under ``/api`` behind
:func:`require_principal`. It deliberately does NOT go on the ``/v1/calls``
router, which carries ``require_scope("write")`` on the router itself: hanging a
read off it would demand a write scope to look at a table.

One response, tests embedded
----------------------------

Runs come back with their tests inside them rather than behind a second
``/runs/{id}/tests`` call. Two reasons, and the second is the load-bearing one:

* A load run is a handful of rows, so the second round trip buys nothing.
* The dashboard's demo mode answers by PATHNAME ONLY, with the query string
  stripped. A per-run path or a query parameter cannot be fixtured, so a shape
  that needs one is a shape the demo build cannot render. Embedding keeps this
  to a single fixturable path.

Provenance travels with the rows
--------------------------------

Every run carries ``data_provenance``, derived from whether it holds a real
artifact checksum, exactly as the report derives it. A reader must not have to
open a report to find out whether the numbers on screen describe anything that
happened.
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
    prefix="/loadtest",
    tags=["dashboard"],
    dependencies=[Depends(require_principal)],
)

# One read per run for its tests, plus one for the list. The cap bounds the
# query count rather than only the payload.
DEFAULT_LIMIT = 20
MAX_LIMIT = 100


@router.get("/runs")
async def list_load_runs(
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    project: str | None = Query(None),
    gateway: Gateway = Depends(get_gateway),
    principal: Principal = Depends(require_principal),
) -> dict[str, Any]:
    """Imported load runs, newest first, each with its tests.

    Shape: ``{"runs": [<load_runs row> + {"data_provenance": str, "tests":
    [<load_run_tests row>]}]}``. No stored field is renamed, added or dropped;
    ``data_provenance`` is the one derived value and it is derived here the same
    way the report derives it, from the checksum's presence.

    503 when storage is disabled, rather than an empty list. "Nothing has been
    imported" and "this deployment records nothing" are different facts, and a
    reader must not be shown the first when the second is true.
    """
    if gateway.storage is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "storage is not configured, so no load run could have been "
                "imported. This is not an empty list of runs."
            ),
        )

    runs = await gateway.storage.list_load_runs(project=project, limit=limit)
    out: list[dict[str, Any]] = []
    for run in runs:
        tests = await gateway.storage.list_load_run_tests(run["id"])
        out.append(
            {
                **run,
                # Derived, never stored as a flag: nothing can claim
                # measured-ness without the artifact behind it.
                "data_provenance": (
                    "measured" if run.get("artifact_sha256") else "synthetic"
                ),
                "tests": tests,
            }
        )
    return {"runs": out}
