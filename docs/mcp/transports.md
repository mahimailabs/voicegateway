---
title: Transports
description: VoiceGateway's MCP server supports stdio (local subprocess) and HTTP/SSE (network). Choose based on whether your agent runs locally or connects remotely.
---

The MCP server supports two transport modes: stdio and HTTP/SSE. The right choice depends on where your agent runs and how many agents share the gateway.

## stdio

The stdio transport communicates over the process's standard input and output streams. The agent launches the MCP server as a subprocess and exchanges MCP protocol messages directly over stdin/stdout.

```bash
voicegw mcp --transport stdio
```

### Characteristics

- No network involved. Communication is over stdin/stdout pipes.
- No authentication. The agent owns the process, so there is no untrusted network boundary.
- Single agent per process. One process serves one agent session.
- Automatic lifecycle. The agent starts and stops the server as needed.

### When to use stdio

- Local development with Claude Code, Cursor, or Codex.
- Single-developer workflows.
- CI/CD scripts that need to query gateway state.

### How it works

1. The agent starts `voicegw mcp --transport stdio` as a subprocess.
2. The agent writes MCP JSON-RPC messages to the process's stdin.
3. The MCP server writes responses to stdout.
4. When the agent session ends, the process exits.

## HTTP/SSE

The HTTP/SSE transport runs a web server that agents connect to over the network. It uses Server-Sent Events (SSE) for the server-to-client stream and HTTP POST for client-to-server messages.

```bash
voicegw mcp --transport http --host 0.0.0.0 --port 8090
```

### Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/sse` | GET | SSE connection point. The agent opens a long-lived connection here to receive messages. |
| `/messages/` | POST | The agent sends tool calls and other MCP messages here. |

### Characteristics

- Network-based. Agents connect over HTTP.
- Optional bearer token authentication. Controlled by `VOICEGW_MCP_TOKEN`. See [Authentication](/mcp/authentication).
- Multiple simultaneous agents. Several agents can connect at the same time.
- Persistent. The server runs until you stop it manually.

### When to use HTTP/SSE

- Shared team gateways where multiple developers need access.
- Remote agents that cannot launch local processes.
- Production environments behind a reverse proxy with TLS.

## Transport comparison

| Feature | stdio | HTTP/SSE |
|---|---|---|
| Network required | No | Yes |
| Authentication | None | Bearer token (optional) |
| Concurrent agents | 1 | Many |
| Agent launches server | Yes | No (run separately) |
| Setup complexity | Minimal | Moderate |
| Best for | Local dev | Team / production |

## Configuration examples

### stdio with Claude Code

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

### HTTP/SSE with authentication

Start the server:

```bash
export VOICEGW_MCP_TOKEN=my-secret-token
voicegw mcp --transport http --host 0.0.0.0 --port 8090
```

Agent config:

```json
{
  "mcpServers": {
    "voicegateway": {
      "url": "http://gateway.internal:8090/sse",
      "headers": {
        "Authorization": "Bearer my-secret-token"
      }
    }
  }
}
```

<Tip>
The default transport when you run `voicegw mcp` with no flags is stdio. You only need to pass `--transport stdio` explicitly when the agent config requires the flag.
</Tip>

## Next steps

- [Authentication](/mcp/authentication): secure the HTTP transport with a bearer token.
- [Setup](/mcp/setup): full per-agent configuration examples.
- [CLI reference: mcp](/cli/mcp): all flags for `voicegw mcp`.
