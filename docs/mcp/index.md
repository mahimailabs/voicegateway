# MCP Server

VoiceGateway includes a built-in [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) server that lets AI coding agents inspect, configure, and manage your voice AI gateway without leaving their workflow.

## What is MCP?

MCP is an open protocol that allows AI agents to use tools provided by external servers. When you connect an agent like Claude Code or Cursor to VoiceGateway's MCP server, the agent gains access to 17 tools for querying gateway state and performing administrative operations.

## Tools Overview

The MCP server exposes tools in four categories:

### Observability (read-only)

| Tool | Description |
|---|---|
| `get_health` | Gateway health, uptime, version |
| `get_provider_status` | Provider configuration status |
| `get_costs` | Cost summary by period/project |
| `get_latency_stats` | Latency percentiles and per-model stats |
| `get_logs` | Recent request logs with filters |

### Provider Management

| Tool | Description |
|---|---|
| `list_providers` | List all configured providers |
| `get_provider` | Details for one provider |
| `test_provider` | Live connectivity test |
| `add_provider` | Register a new provider |
| `delete_provider` | Remove a managed provider (destructive) |

### Model Management

| Tool | Description |
|---|---|
| `list_models` | List all registered models |
| `register_model` | Register a new model |
| `delete_model` | Remove a managed model (destructive) |

### Project Management

| Tool | Description |
|---|---|
| `list_projects` | List projects with today's stats |
| `get_project` | Full project details and cost trends |
| `create_project` | Create a new project |
| `delete_project` | Remove a managed project (destructive) |

## Safety Features

Destructive tools (`delete_provider`, `delete_model`, `delete_project`) implement a two-phase confirmation pattern:

1. **Preview phase**: Called without `confirm=True`, the tool returns a preview of the impact (affected models, projects, total spend, etc.).
2. **Confirm phase**: Called with `confirm=True`, the deletion is performed.

This ensures agents always show the user what will happen before making irreversible changes.

## Quick Start

```bash
# Install MCP dependencies
pip install "voicegateway[mcp]"

# Start with stdio (for local agents)
voicegw mcp

# Start with HTTP/SSE (for remote agents)
VOICEGW_MCP_TOKEN=my-token voicegw mcp --transport http --port 8090
```

## Next Steps

- [Setup guide](/mcp/setup) -- configure Claude Code, Cursor, or Codex
- [Transports](/mcp/transports) -- stdio vs HTTP/SSE
- [Authentication](/mcp/authentication) -- securing the HTTP transport
- Tool references: [Observability](/mcp/tools/observability), [Providers](/mcp/tools/providers), [Models](/mcp/tools/models), [Projects](/mcp/tools/projects)
