# voicegw mcp

Start the VoiceGateway MCP (Model Context Protocol) server.

## Purpose

The `mcp` command starts an MCP server that exposes 17 tools for AI coding agents to inspect and manage the gateway. Agents like Claude Code, Cursor, and Codex can use these tools to check provider status, view costs, register models, create projects, and more -- all without leaving their workflow.

## Syntax

```bash
voicegw mcp [OPTIONS]
```

## Options

| Flag | Short | Type | Default | Description |
|---|---|---|---|---|
| `--transport` | `-t` | `string` | `stdio` | Transport layer: `stdio` for local agents, `http` for remote/SSE. |
| `--host` | | `string` | `127.0.0.1` | HTTP bind host (only used with `--transport http`). |
| `--port` | `-p` | `integer` | `8090` | HTTP bind port (only used with `--transport http`). |
| `--config` | `-c` | `string` | `null` | Path to `voicegw.yaml`. Auto-discovered if omitted. |

## Prerequisites

The `mcp` extra must be installed:

```bash
pip install "voicegateway[mcp]"
```

For the HTTP transport, the `dashboard` extra is also needed (for `uvicorn` and `starlette`):

```bash
pip install "voicegateway[mcp,dashboard]"
```

## Transport Modes

### stdio (default)

Used by local coding agents that launch the MCP server as a subprocess. The agent communicates over stdin/stdout using the MCP protocol.

```bash
voicegw mcp --transport stdio
```

No authentication is required for stdio transport.

### HTTP/SSE

Used for remote access or shared team gateways. The server exposes an SSE endpoint at `/sse` and accepts messages at `/messages/`.

```bash
voicegw mcp --transport http --port 8090
```

Authentication is enabled by setting the `VOICEGW_MCP_TOKEN` environment variable. See [MCP Authentication](/mcp/authentication) for details.

## Examples

### Start with stdio for Claude Code

```bash
voicegw mcp
```

Claude Code would be configured to launch this command as its MCP server.

### Start HTTP server for remote agents

```bash
VOICEGW_MCP_TOKEN=my-secret-token voicegw mcp --transport http --port 8090
```

### Start on a custom host and port

```bash
voicegw mcp -t http --host 0.0.0.0 --port 9000
```

### Use a specific config file

```bash
voicegw mcp --config /etc/voicegateway/voicegw.yaml
```

## Available Tools

The MCP server exposes 17 tools across four categories:

| Category | Tools |
|---|---|
| Observability | `get_health`, `get_provider_status`, `get_costs`, `get_latency_stats`, `get_logs` |
| Providers | `list_providers`, `get_provider`, `test_provider`, `add_provider`, `delete_provider` |
| Models | `list_models`, `register_model`, `delete_model` |
| Projects | `list_projects`, `get_project`, `create_project`, `delete_project` |

See the [MCP tools reference](/mcp/tools/observability) for full documentation.

## Related Commands

- [`voicegw serve`](/cli/serve) -- the HTTP API (MCP is a separate server)
- [`voicegw dashboard`](/cli/dashboard) -- the web UI
- [`voicegw status`](/cli/status) -- quick status check before starting MCP
