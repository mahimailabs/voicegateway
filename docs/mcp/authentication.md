---
title: Authentication
description: Secure the VoiceGateway MCP HTTP/SSE transport with the VOICEGW_MCP_TOKEN bearer token environment variable.
---

The MCP server supports optional bearer token authentication for the HTTP/SSE transport. The stdio transport never checks authentication because it is only accessible to the local process that launched it.

## How it works

Authentication is controlled by the `VOICEGW_MCP_TOKEN` environment variable.

| State | Behavior |
|---|---|
| `VOICEGW_MCP_TOKEN` is set | All HTTP requests must include a matching `Authorization: Bearer <token>` header. |
| `VOICEGW_MCP_TOKEN` is not set | Authentication is disabled; all requests pass through. |

The check runs on both the SSE connection endpoint (`GET /sse`) and the message endpoint (`POST /messages/`). It uses `hmac.compare_digest` for constant-time comparison to prevent timing attacks.

## Setting up authentication

### 1. Generate a token

```bash
# Python (recommended)
python -c "import secrets; print(secrets.token_urlsafe(32))"

# OpenSSL
openssl rand -base64 32
```

### 2. Start the server with the token

```bash
export VOICEGW_MCP_TOKEN=dGhpcyBpcyBhIHNlY3JldCB0b2tlbg
voicegw mcp --transport http --port 8090
```

### 3. Configure agents to send the token

In your agent's MCP config:

```json
{
  "mcpServers": {
    "voicegateway": {
      "url": "http://your-server:8090/sse",
      "headers": {
        "Authorization": "Bearer dGhpcyBpcyBhIHNlY3JldCB0b2tlbg"
      }
    }
  }
}
```

## HTTP response codes

| Scenario | Response |
|---|---|
| Valid token present | Request proceeds normally. |
| Token missing | `401 Unauthorized` with body `Missing bearer token`. |
| Token incorrect | `401 Unauthorized` with body `Invalid token`. |
| Wrong auth scheme (e.g. `Basic`) | `401 Unauthorized` with body `Missing bearer token`. |

Only the `Bearer` scheme is accepted.

## When to disable authentication

Leaving `VOICEGW_MCP_TOKEN` unset is appropriate when:

- Running locally during development.
- The server is on an internal network protected by a VPN.
- Authentication is handled upstream by a reverse proxy.

<Warning>
When the HTTP transport is accessible from a public network, always set `VOICEGW_MCP_TOKEN` and terminate TLS at a reverse proxy. See the [Setup guide](/mcp/setup) for a sample Nginx config.
</Warning>

## stdio transport

The stdio transport bypasses authentication entirely. The agent launches the MCP server as a subprocess, so there is no network boundary to protect. `VOICEGW_MCP_TOKEN` is ignored for stdio connections.

## Environment variable reference

| Variable | Required | Description |
|---|---|---|
| `VOICEGW_MCP_TOKEN` | No | Bearer token for HTTP/SSE authentication. If unset, auth is disabled. |

See [Environment variables](/configuration/environment-variables) for the full list of VoiceGateway env vars.
