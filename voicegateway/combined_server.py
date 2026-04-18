"""Combined server — runs API, Dashboard, and MCP SSE on a single port.

Used for Fly.io and other single-process deployments where multiple ports
are not practical. Mounts:
  /          → Dashboard (Vite-built frontend)
  /health    → Health check
  /v1/*      → HTTP API
  /mcp/sse   → MCP SSE endpoint
  /mcp/messages/* → MCP message endpoint
"""

from __future__ import annotations

import logging
import os
from typing import Any

from voicegateway.core.gateway import Gateway
from voicegateway.server import build_app

logger = logging.getLogger(__name__)


def build_combined_app(gateway: Gateway) -> Any:
    """Build a FastAPI app that serves API + Dashboard + MCP SSE."""
    app = build_app(gateway)

    # Mount MCP SSE endpoints if mcp package is installed
    try:
        from mcp.server.sse import SseServerTransport
    except ImportError:
        logger.info("MCP SSE transport not available (mcp package not installed)")
        return app

    try:
        from starlette.requests import Request
        from starlette.responses import JSONResponse, Response
        from starlette.routing import Mount, Route

        from voicegateway.mcp.auth import AuthError, check_authorization_header
        from voicegateway.mcp.server import create_server

        mcp_server = create_server(gateway)
        sse = SseServerTransport("/mcp/messages/")

        async def handle_mcp_sse(request: Request) -> Response:
            try:
                check_authorization_header(request.headers.get("Authorization"))
            except AuthError as exc:
                return JSONResponse(
                    {"error": {"code": "UNAUTHORIZED", "message": exc.message}},
                    status_code=exc.status_code,
                )

            async with sse.connect_sse(
                request.scope, request.receive, request._send
            ) as (read_stream, write_stream):
                await mcp_server.run(
                    read_stream,
                    write_stream,
                    mcp_server.create_initialization_options(),
                )
            return Response()

        async def mcp_messages_app(scope: Any, receive: Any, send: Any) -> None:
            headers = dict(scope.get("headers") or [])
            auth_header_bytes = headers.get(b"authorization")
            auth_header = auth_header_bytes.decode() if auth_header_bytes else None
            try:
                check_authorization_header(auth_header)
            except AuthError as exc:
                import json

                body = json.dumps(
                    {"error": {"code": "UNAUTHORIZED", "message": exc.message}}
                ).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": exc.status_code,
                        "headers": [
                            (b"content-type", b"application/json"),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
            await sse.handle_post_message(scope, receive, send)

        # Mount MCP routes onto the FastAPI app
        app.routes.insert(0, Route("/mcp/sse", endpoint=handle_mcp_sse))
        app.routes.insert(1, Mount("/mcp/messages/", app=mcp_messages_app))

        logger.info("MCP SSE endpoint mounted at /mcp/sse")

    except ImportError:
        logger.info("MCP module not installed, skipping SSE mount")

    # Also mount the dashboard if it exists
    try:
        import dashboard.api.main as dash_mod

        dash_mod._gateway = gateway

        # Mount dashboard API routes
        for route in dash_mod.app.routes:
            app.routes.append(route)

        logger.info("Dashboard API mounted")
    except ImportError:
        logger.info("Dashboard not installed, skipping")

    return app


def main() -> None:
    """Entry point for the combined server."""
    import uvicorn

    config_path = os.environ.get("VOICEGW_CONFIG")
    host = os.environ.get("VOICEGW_HOST", "0.0.0.0")
    port = int(os.environ.get("VOICEGW_PORT", "8080"))

    gw = Gateway(config_path=config_path)
    app = build_combined_app(gw)

    logger.info("Starting combined server on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
