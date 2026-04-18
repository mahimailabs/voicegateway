# Agent Setup

This guide covers how to connect different AI coding agents to VoiceGateway's MCP server.

## Prerequisites

Install the MCP dependencies:

```bash
pip install "voicegateway[mcp]"
```

For the HTTP transport, also install the dashboard extra:

```bash
pip install "voicegateway[mcp,dashboard]"
```

## Claude Code

Claude Code connects via stdio transport. Add VoiceGateway to your project's `.mcp.json` or global config.

### Project-level configuration

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

If VoiceGateway is installed in a virtual environment, use the full path:

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

After adding the config, restart Claude Code. The agent will automatically discover the 17 VoiceGateway tools.

## Cursor

Cursor supports MCP servers via its settings. Add VoiceGateway to your Cursor MCP configuration:

### stdio transport

In your Cursor MCP config (`.cursor/mcp.json` or global settings):

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

### HTTP transport

If you are running the MCP server remotely:

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

Codex supports MCP via stdio. Configure it in your project:

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

## Remote / Team Setup

For shared team gateways, run the MCP server over HTTP/SSE so multiple agents can connect:

### 1. Start the server

```bash
export VOICEGW_MCP_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
echo "Token: $VOICEGW_MCP_TOKEN"

voicegw mcp --transport http --host 0.0.0.0 --port 8090
```

### 2. Connect agents

Each team member configures their agent to connect to the shared URL:

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

### 3. Secure with HTTPS

In production, put the MCP server behind a reverse proxy with TLS:

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

## Verifying the Connection

Once connected, ask the agent to check the gateway health:

> "Check VoiceGateway health"

The agent should call `get_health` and return something like:

```
Gateway is running (v0.1.0, uptime 1234.5s).
3 providers configured, 2 projects, cost tracking enabled.
```
