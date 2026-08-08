---
title: MCP Server
description: VoiceGateway's built-in Model Context Protocol server for inspecting and configuring the gateway from Claude Code, Cursor, Codex, or any MCP-compatible agent.
---

VoiceGateway ships a built-in [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) server (`voicegw mcp`). Connect an MCP-compatible coding agent and check gateway health, query costs and latency, or create and read back projects, in natural language.

## Tool surface

The server exposes 25 tools across five categories. Ten are visible by default; the rest need `VOICEGW_MCP_ADMIN=1` set on the server process.

The default set is the framework-agnostic surface: reads (health, costs, latency, logs, models, projects) plus project and rate-card writes. The admin set is the legacy provider-config surface (`add_provider`, `test_provider`, the per-project `vg_*` key tools) plus every destructive delete. It stays admin-gated because VoiceGateway no longer constructs a provider class to route a request: `attach()`/`guard()` meter the native STT/LLM/TTS instance you pass in, by `model_id`, and never touch these classes. What still runs in production is `health_check()` (reached here through `test_provider` / `vg_test_provider_key`, and also by `voicegw doctor` and `POST /v1/providers/{id}/test`).

| Category | Default | Admin-only (`VOICEGW_MCP_ADMIN=1`) |
|---|---|---|
| Observability | `get_health`, `get_costs`, `get_latency_stats`, `get_logs` | `get_provider_status` |
| Models | `list_models` | `register_model`, `delete_model` |
| Projects | `list_projects`, `get_project`, `create_project` | `delete_project` |
| Rate card | `get_rate_card`, `set_rate_card_override` | `delete_rate_card_override` |
| Providers | none | `list_providers`, `get_provider`, `test_provider`, `add_provider`, `delete_provider`, `vg_add_provider`, `vg_remove_provider`, `vg_list_providers`, `vg_set_provider_key`, `vg_test_provider_key` |

Every `delete_*` tool uses a two-phase confirmation: call it with `confirm=false` (the default) to get a `CONFIRMATION_REQUIRED` error carrying an impact preview, then call again with `confirm=true`.

## Quick start

<CodeGroup>
```bash pip
pip install "voicegateway[dashboard,livekit]"
voicegw mcp --transport stdio
```
```bash uv
uv add "voicegateway[dashboard,livekit]"
voicegw mcp --transport stdio
```
</CodeGroup>

There is no standalone `mcp` extra: the server ships inside `dashboard`. `livekit` is required too, even for Pipecat-only agents: every `voicegw` subcommand imports it at CLI startup. See [Installation](/guide/installation) for the full extras matrix.

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
  <Card title="Transports & auth" href="/mcp/transports">
    stdio vs. HTTP/SSE, and securing the HTTP transport with a bearer token.
  </Card>
  <Card title="Default tools" href="/mcp/tools">
    The ten tools visible without `VOICEGW_MCP_ADMIN`.
  </Card>
  <Card title="Admin tools" href="/mcp/tools-admin">
    Provider config, registration, and delete tools.
  </Card>
</CardGroup>
