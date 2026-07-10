---
title: MCP Server
description: VoiceGateway's built-in Model Context Protocol server lets AI coding agents configure providers, manage projects, and query costs without leaving their workflow.
---

VoiceGateway ships a built-in [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) server. Connect Claude Code, Cursor, Codex, or Cline to the gateway and manage it conversationally. No dashboard required.

## What the MCP server does

The server exposes 17 tools across four categories. Your AI editor can:

- Check gateway health and provider connectivity.
- Register new providers and models.
- Create projects with budgets and routing rules.
- Query cost summaries and request logs.

All destructive tools (`delete_provider`, `delete_model`, `delete_project`) use a two-phase confirmation pattern. The agent always shows you the impact before applying the change.

## Tool categories

| Category | Tools | Purpose |
|---|---|---|
| Observability | `get_health`, `get_provider_status`, `get_costs`, `get_latency_stats`, `get_logs` | Read-only health, cost, and log queries |
| Providers | `list_providers`, `get_provider`, `test_provider`, `add_provider`, `delete_provider` | Configure and test voice AI providers |
| Models | `list_models`, `register_model`, `delete_model` | Manage model registrations |
| Projects | `list_projects`, `get_project`, `create_project`, `delete_project` | Create and track projects with budgets |

## Quick start

<CodeGroup>
```bash pip
pip install "voicegateway[mcp]"
voicegw mcp --transport stdio
```

```bash uv
uv add "voicegateway[mcp]"
voicegw mcp --transport stdio
```
</CodeGroup>

For HTTP/SSE (team or remote setup):

```bash
export VOICEGW_MCP_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
voicegw mcp --transport http --host 0.0.0.0 --port 8090
```

## Explore further

<CardGroup cols={2}>
  <Card title="Setup" href="/mcp/setup">
    Register the server in Claude Code, Cursor, or Codex.
  </Card>
  <Card title="Transports" href="/mcp/transports">
    Choose between stdio (local) and HTTP/SSE (remote).
  </Card>
  <Card title="Authentication" href="/mcp/authentication">
    Secure the HTTP transport with a bearer token.
  </Card>
  <Card title="Observability tools" href="/mcp/tools/observability">
    Health, cost, latency, and log query tools.
  </Card>
  <Card title="Provider tools" href="/mcp/tools/providers">
    Add, test, and remove provider configurations.
  </Card>
  <Card title="Model tools" href="/mcp/tools/models">
    Register and delete model entries.
  </Card>
  <Card title="Project tools" href="/mcp/tools/projects">
    Create projects with budgets and routing stacks.
  </Card>
  <Card title="Claude Code example" href="/examples/claude-code-integration">
    Full walkthrough with Claude Code.
  </Card>
</CardGroup>
