"""VoiceGateway HTTP API server.

``ApplicationBuilder`` constructs the FastAPI app for a given Gateway,
with each concern (layered DI stack, app state, CORS, routers, optional
MCP SSE, optional dashboard SPA) handled by one private method.
``build_app`` stays as the public entry point and is a thin wrapper
over ``ApplicationBuilder.build()``. ``main`` is the CLI entry point
used by ``python -m voicegateway.server.main`` and the Dockerfile.
"""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from voicegateway.core.app_wiring import attach_layered_stack
from voicegateway.core.auth import load_api_keys, resolve_cors_origins
from voicegateway.core.events import lifespan
from voicegateway.server.mcp.transport import mount_sse
from voicegateway.server.routes import api_router, dashboard_router, system_router
from voicegateway.server.static import mount_frontend
from voicegateway.services.ingest_rate_limiter import IngestRateLimiter

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

logger = logging.getLogger(__name__)


class ApplicationBuilder:
    """Construct the VoiceGateway FastAPI app for a given Gateway.

    Each ``_configure_*`` / ``_mount_*`` method handles exactly one
    concern. Optional features (MCP SSE, dashboard) are gated by
    constructor kwargs and skipped entirely when disabled.
    """

    def __init__(
        self,
        gateway: Gateway,
        *,
        enable_mcp_sse: bool = True,
        enable_dashboard: bool = True,
    ) -> None:
        self.gateway = gateway
        self.enable_mcp_sse = enable_mcp_sse
        self.enable_dashboard = enable_dashboard
        self.app: FastAPI = self._make_app()

    def build(self) -> FastAPI:
        """Wire every configured concern onto the FastAPI app and return it.

        Static SPA mounts MUST come last; their ``/{full_path:path}``
        fallback would otherwise shadow any router registered after.
        """
        self._configure_layered_stack()
        self._configure_app_state()
        self._configure_cors()
        self._configure_routers()
        if self.enable_mcp_sse:
            self._mount_mcp_sse()
        if self.enable_dashboard:
            self._mount_dashboard_spa()
        return self.app

    def _make_app(self) -> FastAPI:
        return FastAPI(
            title="VoiceGateway API",
            version="0.6.0",
            description=(
                "HTTP API for VoiceGateway: cost tracking and reconciliation "
                "for LiveKit voice agents."
            ),
            lifespan=lifespan,
        )

    def _configure_layered_stack(self) -> None:
        attach_layered_stack(self.app, self.gateway)

    def _configure_app_state(self) -> None:
        # ``server.api._deps.get_gateway`` and ``require_scope`` read these
        # off app.state instead of closing over them, so each request can
        # resolve the gateway and api_keys without a per-call lookup.
        self.app.state.gateway = self.gateway
        self.app.state.api_keys = load_api_keys(self.gateway.config.auth)
        self.app.state.started_at = time.time()
        ingest_cfg = self.gateway.config.ingest
        self.app.state.ingest_rate_limiter = (
            IngestRateLimiter(
                requests_per_minute=ingest_cfg.requests_per_minute,
                burst=ingest_cfg.burst,
            )
            if ingest_cfg.enabled and ingest_cfg.requests_per_minute > 0
            else None
        )

    def _configure_cors(self) -> None:
        cors_origins = resolve_cors_origins(self.gateway.config.auth)
        if cors_origins == ["*"]:
            logger.warning(
                "CORS: allow_origins=['*']. Set auth.cors_origins in "
                "voicegw.yaml to restrict."
            )
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["Deprecation"],
        )

    def _configure_routers(self) -> None:
        from voicegateway.server.api.dashboard.branding import mount_static_branding

        self.app.include_router(system_router)
        self.app.include_router(api_router)
        self.app.include_router(dashboard_router)
        mount_static_branding(self.app)

    def _mount_mcp_sse(self) -> None:
        mount_sse(self.app, self.gateway)

    def _mount_dashboard_spa(self) -> None:
        mount_frontend(self.app)


def build_app(
    gateway: Gateway,
    *,
    enable_mcp_sse: bool = True,
    enable_dashboard: bool = True,
) -> FastAPI:
    """Build a FastAPI app bound to the given Gateway instance.

    Thin wrapper over :class:`ApplicationBuilder`. By default, mounts
    the MCP SSE transport and the React SPA at ``/``. Pass
    ``enable_mcp_sse=False`` / ``enable_dashboard=False`` to get the
    HTTP-API-only shape (no SPA fallback, no MCP transport).
    """
    return ApplicationBuilder(
        gateway,
        enable_mcp_sse=enable_mcp_sse,
        enable_dashboard=enable_dashboard,
    ).build()


def main() -> None:
    """Entry point for ``python -m voicegateway.server.main``."""
    import uvicorn

    from voicegateway.core.auth import describe_auth
    from voicegateway.core.gateway import Gateway

    config_path = os.environ.get("VOICEGW_CONFIG")
    host = os.environ.get("VOICEGW_HOST", "0.0.0.0")
    port = int(os.environ.get("VOICEGW_PORT", "8080"))

    gw = Gateway(config_path=config_path)
    app = build_app(gw)

    logger.info("Starting VoiceGateway server on %s:%d", host, port)
    logger.info(describe_auth(load_api_keys(gw.config.auth)))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
