"""``voicegw mcp`` command.

Carved out of voicegateway/cli/_legacy.py during the v0.1.0 section-2
refactor. Boots the MCP (Model Context Protocol) server for
agent-managed gateway configuration. Supports stdio (local agents:
Claude Code, Cursor, Codex, Cline) or HTTP/SSE (remote access).
"""

from __future__ import annotations

import asyncio

import typer

from voicegateway.cli._app import app, console
from voicegateway.cli._helpers import _load_gateway


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
    """Start the VoiceGateway MCP server.

    Use stdio transport for local agents (Claude Code, Cursor):

        voicegw mcp --transport stdio

    Use HTTP/SSE for remote access or team gateways:

        voicegw mcp --transport http --port 8090

    Authentication (HTTP only) via VOICEGW_MCP_TOKEN env var.
    """
    if transport not in ("stdio", "http"):
        console.print(
            f"[red]Unknown transport: {transport}. Use 'stdio' or 'http'.[/red]"
        )
        raise typer.Exit(1)

    try:
        from voicegateway.mcp.server import serve_http, serve_stdio
    except ImportError as e:
        console.print(
            "[red]MCP dependencies not installed. "
            "Run: pip install 'voicegateway[mcp]'[/red]"
        )
        raise typer.Exit(1) from e

    gw = _load_gateway(config)

    if transport == "stdio":
        asyncio.run(serve_stdio(gw))
    else:
        console.print(
            f"[green]VoiceGateway MCP server listening on http://{host}:{port}/sse[/green]"
        )
        asyncio.run(serve_http(gw, host=host, port=port))
