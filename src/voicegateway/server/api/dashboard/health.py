"""Plumbing prover for the ``/api/*`` router family.

This endpoint exists so the dashboard router aggregation can be tested
end-to-end before any real handler moves in. It returns a static body
and never touches the gateway, the database, or any optional dependency.

The endpoint is intentionally namespaced under ``/_dashboard/`` so it
cannot collide with any future user-facing resource at ``/api/health``.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["dashboard"])


@router.get("/_dashboard/health")
async def dashboard_health() -> dict[str, bool]:
    return {"ok": True}
