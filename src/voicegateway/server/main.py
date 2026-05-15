"""VoiceGateway HTTP API server.

``build_app`` returns a FastAPI app with every domain router mounted.
MCP SSE and the dashboard sub-app are mounted by default and can be
turned off with the ``enable_mcp_sse`` and ``enable_dashboard`` kwargs.
``main`` is the CLI entry point used by ``python -m
voicegateway.server.main`` and the Dockerfile.
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
from voicegateway.server.mcp.transport import mount_sse
from voicegateway.server.routes import api_router, system_router

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

logger = logging.getLogger(__name__)


def _mount_dashboard(app: FastAPI, gateway: Gateway) -> None:
    """Attach the dashboard FastAPI sub-app onto the main app's routes."""
    try:
        import dashboard.api.main as dash_mod
    except ImportError:
        logger.info("Dashboard not installed, skipping")
        return
    dash_mod._gateway = gateway
    for route in dash_mod.app.routes:
        app.routes.append(route)
    logger.info("Dashboard API mounted")


def build_app(
    gateway: Gateway,
    *,
    enable_mcp_sse: bool = True,
    enable_dashboard: bool = True,
) -> FastAPI:
    """Build a FastAPI app bound to the given Gateway instance.

    By default, mounts the MCP SSE transport and the dashboard sub-app.
    Pass ``enable_mcp_sse=False`` / ``enable_dashboard=False`` to get the
    HTTP-API-only shape.
    """
    app = FastAPI(
        title="VoiceGateway API",
        version="0.6.0",
        description=(
            "HTTP API for VoiceGateway: cost tracking and reconciliation "
            "for LiveKit voice agents."
        ),
    )

    attach_layered_stack(app, gateway)

    api_keys = load_api_keys(gateway.config.auth)
    cors_origins = resolve_cors_origins(gateway.config.auth)
    if cors_origins == ["*"]:
        logger.warning(
            "CORS: allow_origins=['*']. Set auth.cors_origins in "
            "voicegw.yaml to restrict."
        )

    # Bind app-wide state so ``get_gateway`` and ``require_scope`` (in
    # ``server.api._deps``) read gateway + api_keys without closure capture.
    app.state.gateway = gateway
    app.state.api_keys = api_keys
    app.state.started_at = time.time()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Deprecation"],
    )

    app.include_router(system_router)
    app.include_router(api_router)

    if enable_mcp_sse:
        mount_sse(app, gateway)
    if enable_dashboard:
        _mount_dashboard(app, gateway)

    return app


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
