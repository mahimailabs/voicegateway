---
title: Agent Setup
description: Connect Claude Code, Cursor, Codex, or any MCP-compatible agent to VoiceGateway's MCP server using stdio or HTTP/SSE.
---

This page shows how to register VoiceGateway's MCP server in popular AI coding agents.

## Prerequisites

Install the MCP extra:

<CodeGroup>
```bash pip
pip install "voicegateway[dashboard]"
```

```bash uv
uv add "voicegateway[dashboard]"
```
</CodeGroup>

For the HTTP/SSE transport, also install the dashboard extra:

<CodeGroup>
```bash pip
pip install "voicegateway[dashboard]"
```

```bash uv
uv add "voicegateway[dashboard]"
```
</CodeGroup>

## Claude Code

Claude Code reads MCP server definitions from `.mcp.json` in the project root (project-scoped) or from your global Claude config (user-scoped).

### Via the CLI

```bash
claude mcp add voicegateway --command "voicegw mcp --transport stdio"
```

### Via .mcp.json

Create or edit `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "voicegateway": {
      "command": "voicegw",
      "args": ["mcp", "--transport", "stdio"]
    }
  }
}
```

### With a custom config path

```json
{
  "mcpServers": {
    "voicegateway": {
      "command": "voicegw",
      "args": ["mcp", "--transport", "stdio", "--config", "/path/to/voicegw.yaml"]
    }
  }
}
```

### Using a virtual environment

If VoiceGateway is installed in a virtual environment, use the full binary path:

```json
{
  "mcpServers": {
    "voicegateway": {
      "command": "/path/to/venv/bin/voicegw",
      "args": ["mcp"]
    }
  }
}
```

Restart Claude Code after saving. It will discover all 17 VoiceGateway tools automatically.

## Cursor

Add VoiceGateway to `.cursor/mcp.json` (project) or the global Cursor MCP settings.

### stdio transport

```json
{
  "mcpServers": {
    "voicegateway": {
      "command": "voicegw",
      "args": ["mcp", "--transport", "stdio"]
    }
  }
}
```

### HTTP/SSE transport

If the gateway runs on a remote host:

```json
{
  "mcpServers": {
    "voicegateway": {
      "url": "http://your-server:8090/sse",
      "headers": {
        "Authorization": "Bearer your-token-here"
      }
    }
  }
}
```

## Codex (OpenAI CLI)

Codex supports MCP via stdio. Add VoiceGateway to its MCP config:

```json
{
  "mcpServers": {
    "voicegateway": {
      "command": "voicegw",
      "args": ["mcp", "--transport", "stdio"]
    }
  }
}
```

## Remote / team setup

Run the MCP server over HTTP/SSE so multiple developers can share one gateway instance.

### 1. Generate a token and start the server

```bash
export VOICEGW_MCP_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
echo "Token: $VOICEGW_MCP_TOKEN"

voicegw mcp --transport http --host 0.0.0.0 --port 8090
```

### 2. Point agents at the shared URL

```json
{
  "mcpServers": {
    "voicegateway": {
      "url": "http://gateway.internal:8090/sse",
      "headers": {
        "Authorization": "Bearer <the-shared-token>"
      }
    }
  }
}
```

### 3. Add TLS in production

Put the MCP server behind a reverse proxy with TLS termination:

```nginx
server {
    listen 443 ssl;
    server_name mcp.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8090;
        proxy_set_header Host $host;
        proxy_set_header Connection '';
        proxy_http_version 1.1;
        chunked_transfer_encoding off;
        proxy_buffering off;
    }
}
```

<Warning>
Never expose the HTTP transport on a public network without setting `VOICEGW_MCP_TOKEN` and enabling TLS. See [Authentication](/mcp/authentication) for details.
</Warning>

## Verify the connection

Once connected, ask your agent to check the gateway:

> "Check VoiceGateway health"

The agent calls `get_health` and returns something like:

```
Gateway is running (uptime 1234.5s).
3 providers configured, 2 projects, cost tracking enabled.
```

## Next steps

- [Transports](/mcp/transports): stdio vs HTTP/SSE comparison.
- [Authentication](/mcp/authentication): securing the HTTP transport.
- [CLI reference: mcp](/cli/mcp): all flags for `voicegw mcp`.
