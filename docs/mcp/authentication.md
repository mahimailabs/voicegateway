# Authentication

The MCP server supports optional Bearer token authentication for the HTTP/SSE transport. The stdio transport never checks authentication since it is only accessible to the local process that launched it.

## Overview

Authentication is controlled by the `VOICEGW_MCP_TOKEN` environment variable:

- **Set** -- all HTTP requests must include a matching `Authorization: Bearer <token>` header.
- **Not set** -- authentication is disabled and all requests are accepted.

## Setting Up Authentication

### 1. Generate a token

Use any secure random generator:

```bash
# Python
python -c "import secrets; print(secrets.token_urlsafe(32))"

# OpenSSL
openssl rand -base64 32

# Example output: dGhpcyBpcyBhIHNlY3JldCB0b2tlbg
```

### 2. Start the server with the token

```bash
export VOICEGW_MCP_TOKEN=dGhpcyBpcyBhIHNlY3JldCB0b2tlbg
voicegw mcp --transport http --port 8090
```

### 3. Configure agents to send the token

In your agent's MCP configuration:

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

## How It Works

The authentication middleware checks the `Authorization` header on both the SSE connection (`GET /sse`) and the message endpoint (`POST /messages/`). The check uses `hmac.compare_digest` for constant-time comparison to prevent timing attacks.

### Valid request

```
GET /sse HTTP/1.1
Authorization: Bearer dGhpcyBpcyBhIHNlY3JldCB0b2tlbg
```

### Missing token

```
GET /sse HTTP/1.1
```

Returns `401 Unauthorized` with body: `Missing bearer token`.

### Invalid token

```
GET /sse HTTP/1.1
Authorization: Bearer wrong-token
```

Returns `401 Unauthorized` with body: `Invalid token`.

### Malformed header

```
GET /sse HTTP/1.1
Authorization: Basic dXNlcjpwYXNz
```

Returns `401 Unauthorized` with body: `Missing bearer token` (only `Bearer` scheme is accepted).

## When Authentication is Disabled

If `VOICEGW_MCP_TOKEN` is not set (or is an empty string), all requests pass through without any authentication check. This is the default and is appropriate for:

- Local development.
- Internal networks behind a VPN.
- Environments where authentication is handled by a reverse proxy.

::: warning
When running the HTTP transport on a publicly accessible network, always set `VOICEGW_MCP_TOKEN` and use HTTPS (via a reverse proxy).
:::

## stdio Transport

The stdio transport bypasses authentication entirely. Since the agent launches the MCP server as a subprocess, there is no network boundary to protect. The `VOICEGW_MCP_TOKEN` variable is ignored for stdio connections.

## Environment Variable Reference

| Variable | Required | Description |
|---|---|---|
| `VOICEGW_MCP_TOKEN` | No | Bearer token for HTTP/SSE authentication. If unset, auth is disabled. |
