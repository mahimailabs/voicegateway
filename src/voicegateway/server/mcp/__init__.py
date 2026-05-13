"""VoiceGateway MCP server — manage the gateway from coding agents."""

from voicegateway.server.mcp.server import create_server, serve_http, serve_stdio

__all__ = ["create_server", "serve_stdio", "serve_http"]
