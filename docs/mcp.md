# VoiceGateway MCP Server

The VoiceGateway MCP server exposes 17 tools that let coding agents
(Claude Code, Cursor, Codex, Cline, …) manage your gateway
conversationally. No dashboards, no YAML editing: talk to the agent, and
it drives the gateway via well-typed tool calls.

## Install

```bash
pip install "voicegateway[mcp]"
```

## Start the server

### stdio (local agents)

```bash
voicegw mcp --transport stdio
```

This is what Claude Code, Cursor, and other local-first agents speak. It's
stateless, secure-by-default (same process owner), and doesn't expose a
network port.

### HTTP/SSE (remote / team)

```bash
voicegw mcp --transport http --port 8090
```

Serves MCP over Server-Sent Events at `http://HOST:8090/sse` (and the
message endpoint at `/messages/`). Use this when the agent and gateway run
on different machines, or when multiple engineers share one gateway.

## Authentication

**stdio never requires auth** — running as a subprocess of the agent is
already a trust boundary.

**HTTP/SSE** gets auth when you set `VOICEGW_MCP_TOKEN`:

```bash
export VOICEGW_MCP_TOKEN=$(openssl rand -hex 32)
voicegw mcp --transport http
```

All requests must then include `Authorization: Bearer <token>`. Wrong or
missing tokens return HTTP 401. Token comparison uses
`hmac.compare_digest` (constant-time), so timing attacks don't leak
information.

## Agent configuration

### Claude Code

```bash
claude mcp add voicegateway --command "voicegw mcp --transport stdio"
```

Verify it's loaded:

```bash
claude mcp list
```

Then ask Claude: "list my voicegateway providers".

### Cursor

In your Cursor settings (`~/.cursor/mcp.json`):

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

### Codex / Cline

Any MCP-compatible agent works with the stdio transport. Point the agent
at the command `voicegw mcp --transport stdio`.

### Remote agents

For agents that only support HTTP MCP, run the server in HTTP/SSE mode and
give the agent:

- URL: `http://your-host:8090/sse`
- Header: `Authorization: Bearer <VOICEGW_MCP_TOKEN>`

## Tool reference

Every tool description is also available to the agent itself via MCP
introspection. The descriptions below are condensed; call `tools/list` on
the server to see the full docstrings the agent sees.

### Observability (read-only)

| Tool | Purpose |
|------|---------|
| `get_health` | Version, uptime, db/provider/project counts. |
| `get_provider_status` | Configured status per provider (local vs cloud). |
| `get_costs` | Cost summary for `today/week/month/all`, optional project filter. |
| `get_latency_stats` | P50/P95/P99 TTFB, per-model averages. |
| `get_logs` | Recent request rows with filters on project/modality/model/status. |

**Example:**

```
User: "what did tonys-pizza spend yesterday?"
Agent: calls get_costs(period="today", project="tonys-pizza")
      -> {"total_usd": 3.45, "by_provider": {...}, ...}
```

### Providers

| Tool | Purpose |
|------|---------|
| `list_providers` | YAML + managed providers, with source tag. |
| `get_provider` | One provider's detail, API key masked. |
| `test_provider` | Hit the provider's health check, report latency. |
| `add_provider` | Register a new provider (tested before save). |
| `delete_provider` | Remove a managed provider. Requires `confirm=True`. |

`add_provider` tests the credentials before saving. If the key is invalid,
the provider is NOT persisted and the agent gets
`PROVIDER_TEST_FAILED` with a description of the failure.

`delete_provider` cannot delete providers defined in `voicegw.yaml` —
those return `READ_ONLY_RESOURCE` with a hint to edit the YAML.

### Models

| Tool | Purpose |
|------|---------|
| `list_models` | Filter by modality/provider/enabled. |
| `register_model` | Add a model like `openai/gpt-4o-mini`. |
| `delete_model` | Remove a managed model. Requires `confirm=True`. |

### Projects

| Tool | Purpose |
|------|---------|
| `list_projects` | All projects with today's spend and budget status. |
| `get_project` | Full detail with 7-day trend. |
| `create_project` | Name, budget, budget_action, model refs. |
| `delete_project` | Remove a managed project. Requires `confirm=True`. |

`create_project` validates every referenced model. `stt_model`/`llm_model`/
`tts_model` are mutually exclusive with `default_stack`.

## Destructive operations

All `delete_*` tools follow the same pattern:

1. Agent calls `delete_project({"project_id": "tonys-pizza"})` without `confirm`.
2. Tool responds with `CONFIRMATION_REQUIRED` and impact details (total spend,
   request count, last activity, affected models, etc.).
3. Agent shows that to the user verbatim.
4. User approves.
5. Agent calls again with `confirm=True` — now the delete happens.

This keeps the agent from autonomously destroying things.

## Error codes

Tool responses are always JSON. On failure the envelope looks like:

```json
{
  "error": {
    "code": "PROVIDER_NOT_FOUND",
    "message": "No provider 'deepgram2'. Use list_providers to see options.",
    "details": {"provider_id": "deepgram2"}
  }
}
```

| Code | Meaning |
|------|---------|
| `VALIDATION_ERROR` | Bad input per the schema. |
| `PROVIDER_NOT_FOUND` / `MODEL_NOT_FOUND` / `PROJECT_NOT_FOUND` | Lookup miss. |
| `PROVIDER_ALREADY_EXISTS` / `MODEL_ALREADY_EXISTS` / `PROJECT_ALREADY_EXISTS` | Conflict. |
| `READ_ONLY_RESOURCE` | Tried to mutate a YAML-defined resource. |
| `CONFIRMATION_REQUIRED` | Destructive op without `confirm=True`. |
| `PROVIDER_TEST_FAILED` | `add_provider` couldn't reach the provider. |
| `BUDGET_EXCEEDED` | Request blocked by `budget_action: block`. |
| `INTERNAL_ERROR` | Anything else — unexpected exception, check server logs. |

## Adding custom tools (future)

The `voicegateway/mcp/tools/*` layout is designed for extension: add a new
file that exports a list of `ToolDef`s, then register it in
`tools/__init__.py`. Pydantic input schemas live in `mcp/schemas.py` and
are the single source of truth for the tool contract.

If you have a use case the current catalog doesn't cover, open an issue at
https://github.com/mahimailabs/voicegateway/issues — feedback drives the
next tool pass.
