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
    agents,
    api_keys,
    audit_log,
    billing,
    call_observations,
    costs,
    ingest,
    latency,
    livekit_webhook,
    logs,
    metrics,
    models,
    projects,
    providers,
    sessions,
    system,
)
from voicegateway.server.api.dashboard import (
    agents as dashboard_agents,
)
from voicegateway.server.api.dashboard import (
    api_keys as dashboard_api_keys,
)
from voicegateway.server.api.dashboard import (
    auth_status as dashboard_auth_status,
)
from voicegateway.server.api.dashboard import (
    branding as dashboard_branding,
)
from voicegateway.server.api.dashboard import (
    calls as dashboard_calls,
)
from voicegateway.server.api.dashboard import (
    correlation as dashboard_correlation,
)
from voicegateway.server.api.dashboard import (
    costs as dashboard_costs,
)
from voicegateway.server.api.dashboard import (
    diagnostics as dashboard_diagnostics,
)
from voicegateway.server.api.dashboard import (
    health as dashboard_health,
)
from voicegateway.server.api.dashboard import (
    loadtest as dashboard_loadtest,
)
from voicegateway.server.api.dashboard import (
    metrics as dashboard_metrics,
)
from voicegateway.server.api.dashboard import (
    nodes as dashboard_nodes,
)
from voicegateway.server.api.dashboard import (
    projects as dashboard_projects,
)
from voicegateway.server.api.dashboard import (
    replay as dashboard_replay,
)
from voicegateway.server.api.dashboard import (
    server as dashboard_server,
)
from voicegateway.server.api.dashboard import (
    sessions as dashboard_sessions,
)
from voicegateway.server.api.dashboard import (
    status as dashboard_status,
)

system_router = APIRouter()
system_router.include_router(system.router)

api_router = APIRouter(prefix="/v1")
api_router.include_router(models.router)
api_router.include_router(costs.router)
api_router.include_router(billing.router)
api_router.include_router(latency.router)
api_router.include_router(logs.router)
api_router.include_router(sessions.router)
api_router.include_router(projects.router)
api_router.include_router(providers.router)
api_router.include_router(metrics.router)
api_router.include_router(audit_log.router)
api_router.include_router(api_keys.router)
api_router.include_router(ingest.router)
api_router.include_router(agents.router)
# POST /v1/livekit/webhook. Authenticated by the LiveKit webhook signature
# inside the handler, not by require_scope: LiveKit posts it, not an api-key
# holder. See the module docstring for why that guard is unconditional.
api_router.include_router(livekit_webhook.router)
# POST /v1/calls/observations. Authenticated by require_scope("write") declared
# on the router itself (the operator's own agents and load workers carry a
# VoiceGateway api key, unlike LiveKit). Write-only router: a future reader of
# /v1/calls must not inherit the write scope from here.
api_router.include_router(call_observations.router)

dashboard_router = APIRouter(prefix="/api")
dashboard_router.include_router(dashboard_health.router)
dashboard_router.include_router(dashboard_auth_status.router)
dashboard_router.include_router(dashboard_status.router)
dashboard_router.include_router(dashboard_costs.router)
dashboard_router.include_router(dashboard_projects.router)
dashboard_router.include_router(dashboard_sessions.router)
# GET /api/calls. Read-only, authenticated by require_principal declared on the
# router itself. Distinct from /v1/calls/observations above: that router is
# write-only, and neither inherits the other's scope.
dashboard_router.include_router(dashboard_calls.router)
# GET /api/correlation. Read-only, authenticated by require_principal declared on
# the router itself, same as /api/calls above. It reports how often the sessions
# <-> calls join actually resolves; the join fails silently, so nothing else on
# the dashboard would show that it stopped working.
dashboard_router.include_router(dashboard_correlation.router)
# GET /api/nodes. Read-only, authenticated by require_principal declared on the
# router itself, same as /api/calls and /api/correlation above. It serves the
# node scrapes that OVERLAP each recent call's padded window; the padding and
# the three window statuses travel with it, because "within 15 s of this call"
# is a weaker claim than "during this call" and a window nobody scraped is not a
# healthy node.
dashboard_router.include_router(dashboard_nodes.router)
# GET /api/loadtest/runs. Read-only, so it belongs here behind
# require_principal and NOT on the /v1/calls router, which carries
# require_scope("write") on the router itself. Tests are embedded in each run
# rather than served from a per-run path: the demo build answers by pathname
# only, so a parameterised path is a shape it cannot render.
dashboard_router.include_router(dashboard_loadtest.router)
dashboard_router.include_router(dashboard_metrics.router)
dashboard_router.include_router(dashboard_replay.router)
dashboard_router.include_router(dashboard_agents.router)
dashboard_router.include_router(dashboard_diagnostics.router)
dashboard_router.include_router(dashboard_server.router)
dashboard_router.include_router(dashboard_api_keys.router)
dashboard_router.include_router(dashboard_branding.router)


__all__ = ["api_router", "dashboard_router", "system_router"]
