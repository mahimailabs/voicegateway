"""Router aggregator for the VoiceGateway HTTP API.

Three routers compose every endpoint:

- ``system_router`` is mounted at the app root because ``/health`` does
  not carry the ``/v1`` prefix.
- ``api_router`` carries the ``/v1`` prefix and is the parent of every
  domain router under :mod:`voicegateway.server.api`.
- ``dashboard_router`` carries the ``/api`` prefix and is the parent of
  every dashboard router under :mod:`voicegateway.server.api.dashboard`.
  The dashboard endpoints used to live in the standalone
  ``dashboard.api.main`` FastAPI app; they fold into the daemon so a
  single process and port serve both the public HTTP API and the
  dashboard backend.
"""

from __future__ import annotations

from fastapi import APIRouter

from voicegateway.server.api import (
    audit_log,
    costs,
    guardrails,
    latency,
    logs,
    metrics,
    models,
    projects,
    providers,
    sessions,
    system,
    virtual_keys,
)
from voicegateway.server.api.dashboard import (
    health as dashboard_health,
)

system_router = APIRouter()
system_router.include_router(system.router)

api_router = APIRouter(prefix="/v1")
api_router.include_router(models.router)
api_router.include_router(costs.router)
api_router.include_router(latency.router)
api_router.include_router(logs.router)
api_router.include_router(sessions.router)
api_router.include_router(projects.router)
api_router.include_router(providers.router)
api_router.include_router(guardrails.router)
api_router.include_router(metrics.router)
api_router.include_router(audit_log.router)
api_router.include_router(virtual_keys.router)

dashboard_router = APIRouter(prefix="/api")
dashboard_router.include_router(dashboard_health.router)


__all__ = ["api_router", "dashboard_router", "system_router"]
