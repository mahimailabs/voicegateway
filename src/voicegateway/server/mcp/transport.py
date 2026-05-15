"""MCP SSE transport: embed MCP into the main VoiceGateway FastAPI app.

This module is the SSE *mount* used by ``server.main.build_app``. The
related :mod:`voicegateway.server.mcp.server` module hosts an alternative
standalone HTTP/SSE server (``serve_http``) intended for running MCP as
its own process; the two serve different surfaces and can coexist.

No-op when the optional ``mcp`` extra is not installed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI

    from voicegateway.core.gateway import Gateway

logger = logging.getLogger(__name__)


def mount_sse(app: FastAPI, gateway: Gateway) -> None:
    """Attach the MCP SSE transport at /mcp/sse + /mcp/messages."""
    try:
        from mcp.server.sse import SseServerTransport
    except ImportError:
        logger.info("MCP SSE transport not available (mcp package not installed)")
        return

    try:
        from starlette.requests import Request
        from starlette.responses import JSONResponse, Response
        from starlette.routing import Mount, Route

        from voicegateway.server.mcp.auth import (
            AuthError,
            check_authorization_header,
        )
        from voicegateway.server.mcp.server import create_server
    except ImportError:
        logger.info("MCP module not installed, skipping SSE mount")
        return

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
        async with sse.connect_sse(request.scope, request.receive, request._send) as (
            read_stream,
            write_stream,
        ):
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
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await sse.handle_post_message(scope, receive, send)

    app.routes.insert(0, Route("/mcp/sse", endpoint=handle_mcp_sse))
    app.routes.insert(1, Mount("/mcp/messages/", app=mcp_messages_app))
    logger.info("MCP SSE endpoint mounted at /mcp/sse")


__all__ = ["mount_sse"]
