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
    auth_status as dashboard_auth_status,
)
from voicegateway.server.api.dashboard import (
    costs as dashboard_costs,
)
from voicegateway.server.api.dashboard import (
    guardrails as dashboard_guardrails,
)
from voicegateway.server.api.dashboard import (
    health as dashboard_health,
)
from voicegateway.server.api.dashboard import (
    metrics as dashboard_metrics,
)
from voicegateway.server.api.dashboard import (
    projects as dashboard_projects,
)
from voicegateway.server.api.dashboard import (
    providers_by_project as dashboard_providers_by_project,
)
from voicegateway.server.api.dashboard import (
    replay as dashboard_replay,
)
from voicegateway.server.api.dashboard import (
    routing as dashboard_routing,
)
from voicegateway.server.api.dashboard import (
    sessions as dashboard_sessions,
)
from voicegateway.server.api.dashboard import (
    status as dashboard_status,
)
from voicegateway.server.api.dashboard import (
    tenants as dashboard_tenants,
)
from voicegateway.server.api.dashboard import (
    virtual_keys as dashboard_virtual_keys,
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
dashboard_router.include_router(dashboard_auth_status.router)
dashboard_router.include_router(dashboard_status.router)
dashboard_router.include_router(dashboard_costs.router)
dashboard_router.include_router(dashboard_projects.router)
dashboard_router.include_router(dashboard_sessions.router)
dashboard_router.include_router(dashboard_metrics.router)
dashboard_router.include_router(dashboard_guardrails.router)
dashboard_router.include_router(dashboard_replay.router)
dashboard_router.include_router(dashboard_providers_by_project.router)
dashboard_router.include_router(dashboard_tenants.router)
dashboard_router.include_router(dashboard_virtual_keys.router)
dashboard_router.include_router(dashboard_routing.router)


__all__ = ["api_router", "dashboard_router", "system_router"]
