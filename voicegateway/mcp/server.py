"""MCP server bootstrap — wires tool implementations onto stdio and HTTP/SSE transports."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from voicegateway.mcp.errors import MCPToolError

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

logger = logging.getLogger(__name__)


def _format_tool_error(exc: Exception) -> str:
    """Serialise any exception raised inside a tool into the MCP error envelope."""
    if isinstance(exc, MCPToolError):
        return json.dumps(exc.to_dict())
    return json.dumps(
        {
            "error": {
                "code": "INTERNAL_ERROR",
                "message": str(exc) or exc.__class__.__name__,
                "details": {},
            }
        }
    )


def _format_tool_result(result: Any) -> str:
    """Serialise a tool result for transport."""
    return json.dumps(result, default=str, indent=2)


def create_server(gateway: "Gateway") -> Any:
    """Create an MCP server wired to the given gateway instance.

    Returns a low-level ``mcp.server.Server`` with all 17 tools registered.
    Requires the ``mcp`` extra to be installed.
    """
    try:
        from mcp.server import Server
        from mcp.types import TextContent, Tool
    except ImportError as e:
        raise ImportError(
            "mcp package not installed. Run: pip install voicegateway[mcp]"
        ) from e

    from voicegateway.mcp.tools import ALL_TOOLS

    server = Server("voicegateway")

    tool_registry: dict[str, Any] = {t.name: t for t in ALL_TOOLS}

    @server.list_tools()  # type: ignore[misc,no-untyped-call]
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name=t.name,
                description=t.description,
                inputSchema=t.input_schema,
            )
            for t in ALL_TOOLS
        ]

    @server.call_tool()  # type: ignore[misc,no-untyped-call]
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        tool_def = tool_registry.get(name)
        if tool_def is None:
            payload = json.dumps(
                {"error": {"code": "UNKNOWN_TOOL", "message": f"No such tool: {name}", "details": {}}}
            )
            return [TextContent(type="text", text=payload)]

        try:
            result = await tool_def.handler(gateway, arguments or {})
        except MCPToolError as exc:
            return [TextContent(type="text", text=_format_tool_error(exc))]
        except Exception as exc:  # noqa: BLE001
            logger.exception("MCP tool %s raised an unexpected error", name)
            return [TextContent(type="text", text=_format_tool_error(exc))]

        return [TextContent(type="text", text=_format_tool_result(result))]

    return server


async def serve_stdio(gateway: "Gateway") -> None:
    """Run the MCP server over stdio (for local coding agents)."""
    try:
        from mcp.server.stdio import stdio_server
    except ImportError as e:
        raise ImportError(
            "mcp package not installed. Run: pip install voicegateway[mcp]"
        ) from e

    server = create_server(gateway)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


async def serve_http(
    gateway: "Gateway",
    host: str = "127.0.0.1",
    port: int = 8090,
) -> None:
    """Run the MCP server over HTTP/SSE (for remote agents)."""
    try:
        from mcp.server.sse import SseServerTransport
    except ImportError as e:
        raise ImportError(
            "mcp package not installed. Run: pip install voicegateway[mcp]"
        ) from e

    try:
        import uvicorn
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.responses import Response
        from starlette.routing import Mount, Route
    except ImportError as e:
        raise ImportError(
            "fastapi/uvicorn not installed. Run: pip install voicegateway[dashboard]"
        ) from e

    from voicegateway.mcp.auth import AuthError, check_authorization_header

    server = create_server(gateway)
    sse = SseServerTransport("/messages/")

    async def handle_sse(request: Request) -> Response:
        try:
            check_authorization_header(request.headers.get("Authorization"))
        except AuthError as exc:
            return Response(exc.message, status_code=exc.status_code)

        async with sse.connect_sse(request.scope, request.receive, request._send) as (
            read_stream,
            write_stream,
        ):
            await server.run(
                read_stream, write_stream, server.create_initialization_options()
            )
        return Response()

    async def handle_messages(request: Request) -> Response:
        try:
            check_authorization_header(request.headers.get("Authorization"))
        except AuthError as exc:
            return Response(exc.message, status_code=exc.status_code)
        return await sse.handle_post_message(request.scope, request.receive, request._send)

    app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=handle_messages),
        ]
    )

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    await uvicorn.Server(config).serve()
