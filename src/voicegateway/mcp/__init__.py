"""VoiceGateway MCP server — manage the gateway from coding agents.

Provides a Model Context Protocol server exposing 17 tools for listing,
creating, updating, and deleting providers, models, and projects, plus
observability queries (costs, latency, logs, health).

Supports stdio (local) and HTTP/SSE (remote) transports. Authentication
is optional via the VOICEGW_MCP_TOKEN environment variable (HTTP only).
"""

from voicegateway.mcp.server import create_server, serve_http, serve_stdio

__all__ = ["create_server", "serve_stdio", "serve_http"]
