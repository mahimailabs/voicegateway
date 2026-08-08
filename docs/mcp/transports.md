---
title: Transports & Authentication
description: VoiceGateway's MCP server runs over stdio or HTTP/SSE. HTTP/SSE takes an optional VOICEGW_MCP_TOKEN bearer token; stdio has no network boundary to authenticate.
---

## stdio

```bash
voicegw mcp --transport stdio
```

The agent launches the MCP server as a subprocess and exchanges MCP JSON-RPC messages over stdin/stdout. No network, no authentication (the agent already owns the process), one agent per process. This is the default transport: `voicegw mcp` with no flags uses it. Use it for local development with Claude Code, Cursor, or Codex.

## HTTP/SSE

```bash
voicegw mcp --transport http --host 0.0.0.0 --port 8090
```

Runs a Starlette/uvicorn server. Agents open a long-lived `GET /sse` connection for the server-to-client stream and `POST /messages/` to send tool calls. Multiple agents can connect at once; the server runs until you stop it. Use it for a shared team gateway, or a remote agent that can't launch a local subprocess.

<Note>
`voicegw serve` (the main API, `0.0.0.0:8080` by default) mounts this same MCP surface too, at `/mcp/sse` and `/mcp/messages/`, gated by the same `VOICEGW_MCP_TOKEN`. If `voicegw serve` is already running, you don't need a second `voicegw mcp` process for HTTP/SSE access.
</Note>

### Authentication

Controlled by `VOICEGW_MCP_TOKEN`. Unset (the default): every request is accepted. Set: every request needs a matching `Authorization: Bearer <token>` header, checked with `hmac.compare_digest` for constant-time comparison. A missing or malformed header gets `401` with body `Missing bearer token`; a wrong token gets `401 Invalid token`. Only the `Bearer` scheme is accepted. stdio ignores this variable entirely: there's no network boundary to protect.

```bash
export VOICEGW_MCP_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
voicegw mcp --transport http --host 0.0.0.0 --port 8090
```

```json
{
  "mcpServers": {
    "voicegateway": {
      "url": "http://your-server:8090/sse",
      "headers": { "Authorization": "Bearer <the-token>" }
    }
  }
}
```

<Warning>
Set `VOICEGW_MCP_TOKEN` and put the HTTP transport behind a reverse proxy with TLS before exposing it beyond localhost. Leaving it unset is appropriate for local development, an internal network already behind a VPN, or when a reverse proxy handles auth upstream.
</Warning>

Example proxy config (buffering off, so SSE actually streams):

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

## Comparison

| | stdio | HTTP/SSE |
|---|---|---|
| Network | No | Yes |
| Auth | None | Bearer token (optional, `VOICEGW_MCP_TOKEN`) |
| Concurrent agents | 1 | Many |
| Who starts the server | The agent | You, separately |

## Next steps

- [Setup](/mcp/setup): per-agent configuration.
- [CLI reference: mcp](/cli/mcp): all flags for `voicegw mcp`.
- [Environment variables](/configuration/environment-variables): the full VoiceGateway env var list.
