# Transports

VoiceGateway's MCP server supports two transport modes: **stdio** and **HTTP/SSE**. Choose based on whether the agent is running locally or connecting remotely.

## stdio

The stdio transport communicates over the process's standard input and output streams. The agent launches the MCP server as a subprocess and exchanges MCP protocol messages directly.

```bash
voicegw mcp --transport stdio
```

### Characteristics

- **No network involved** -- communication is over stdin/stdout pipes.
- **No authentication** -- since the agent launches the process itself, there is no untrusted network boundary.
- **Single agent** -- one process serves one agent.
- **Automatic lifecycle** -- the agent starts and stops the server as needed.

### When to use

- Local development with Claude Code, Cursor, or Codex.
- Single-developer workflows.
- CI/CD scripts that need to query gateway state.

### How it works

1. The agent starts `voicegw mcp --transport stdio` as a subprocess.
2. The agent writes MCP JSON-RPC messages to the process's stdin.
3. The MCP server writes responses to stdout.
4. When the agent disconnects, the process exits.

## HTTP/SSE

The HTTP/SSE transport runs a web server that agents connect to over the network. It uses Server-Sent Events (SSE) for the server-to-client stream and HTTP POST for client-to-server messages.

```bash
voicegw mcp --transport http --host 0.0.0.0 --port 8090
```

### Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/sse` | `GET` | SSE connection point. The agent opens a long-lived connection here to receive messages. |
| `/messages/` | `POST` | The agent sends tool calls and other MCP messages here. |

### Characteristics

- **Network-based** -- agents connect over HTTP.
- **Authentication supported** -- controlled by the `VOICEGW_MCP_TOKEN` environment variable (Bearer token). See [Authentication](/mcp/authentication).
- **Multiple agents** -- several agents can connect simultaneously.
- **Persistent** -- the server runs until manually stopped.

### When to use

- Shared team gateways.
- Remote agents that cannot launch local processes.
- Production environments behind a reverse proxy with TLS.
- When multiple agents need to connect to the same gateway instance.

## Comparison

| Feature | stdio | HTTP/SSE |
|---|---|---|
| Network required | No | Yes |
| Authentication | None | Bearer token (optional) |
| Concurrent agents | 1 | Many |
| Agent launches server | Yes | No (run separately) |
| Setup complexity | Minimal | Moderate |
| Best for | Local dev | Team / production |

## Configuration Examples

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

```bash
# Start server
export VOICEGW_MCP_TOKEN=my-secret-token
voicegw mcp --transport http --host 0.0.0.0 --port 8090
```

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
