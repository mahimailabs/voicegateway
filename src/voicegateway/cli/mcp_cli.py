"""``voicegw mcp`` command."""

from __future__ import annotations

import asyncio

import typer

from voicegateway.cli._app import app
from voicegateway.cli.base_cli import BaseCli

_cli = BaseCli()


@app.command(name="mcp")
def mcp_cmd(
    transport: str = typer.Option(
        "stdio",
        "--transport",
        "-t",
        help="Transport layer: 'stdio' for local agents, 'http' for remote/SSE.",
    ),
    host: str = typer.Option("127.0.0.1", "--host", help="HTTP bind host (http only)"),
    port: int = typer.Option(8090, "--port", "-p", help="HTTP bind port (http only)"),
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
) -> None:
    """Start the VoiceGateway MCP server."""
    if transport not in ("stdio", "http"):
        _cli.fail(f"Unknown transport: {transport}. Use 'stdio' or 'http'.")

    try:
        # Lazy: gated on the optional ``[mcp]`` extra so the import
        # failure can be presented as a friendly Rich error rather than
        # blocking ``voicegw --help`` from showing this command.
        from voicegateway.server.mcp.server import serve_http, serve_stdio
    except ImportError:
        _cli.fail(
            "MCP dependencies not installed. Run: pip install 'voicegateway[dashboard]'"
        )

    gw = _cli.require_gateway(config)

    if transport == "stdio":
        asyncio.run(serve_stdio(gw))
    else:
        _cli.success(f"VoiceGateway MCP server listening on http://{host}:{port}/sse")
        asyncio.run(serve_http(gw, host=host, port=port))
