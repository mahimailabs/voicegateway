# Claude Code Integration

VoiceGateway includes an MCP (Model Context Protocol) server that lets you manage providers, models, projects, and monitor costs directly from Claude Code using natural language.

## Setup

### 1. Install the MCP Extra

```bash
pip install voicegateway[mcp]
```

### 2. Configure Claude Code

Add VoiceGateway to your Claude Code MCP settings (`~/.claude/settings.json` or your project's `.mcp.json`):

```json
{
  "mcpServers": {
    "voicegateway": {
      "command": "voicegw",
      "args": ["mcp"],
      "env": {
        "VOICEGW_CONFIG": "/path/to/voicegw.yaml"
      }
    }
  }
}
```

### 3. Verify the Connection

Start Claude Code and ask it to check VoiceGateway status. The MCP server exposes 17 tools across four categories: providers, models, projects, and observability.

## Prompt Examples

### 1. Check System Status

**Prompt:**

```
What providers are configured in VoiceGateway? Show me the status of each one.
```

Claude Code will call the `list_providers` tool and display all configured providers, their types (cloud vs. local), and whether they have valid credentials (API keys are shown masked, e.g., `sk-p...z789`).

### 2. Add a New Provider

**Prompt:**

```
Add a Groq provider to VoiceGateway with API key "gsk_abc123xyz789".
Then register the llama-3.3-70b-versatile model as an LLM.
```

Claude Code will:
1. Call `create_provider` with `provider_id="groq"`, `provider_type="groq"`, and the API key
2. Call `register_model` with `model_id="groq/llama-3.3-70b-versatile"`, `modality="llm"`, `provider_id="groq"`
3. The API key is encrypted with Fernet before storage
4. Both actions are recorded in the audit log

### 3. Create a Project with Budget

**Prompt:**

```
Create a new project called "customer-support" with a $25 daily budget.
Set it to throttle mode so it falls back to local models when the budget is exceeded.
Tag it as "production" and "support".
```

Claude Code will call `create_project` with:
- `project_id`: `"customer-support"`
- `name`: `"Customer Support"`
- `daily_budget`: `25.00`
- `budget_action`: `"throttle"`
- `tags`: `["production", "support"]`

### 4. Monitor Costs

**Prompt:**

```
Show me today's costs broken down by project and provider.
Which model is costing us the most?
```

Claude Code will call the `get_costs` tool with `period="today"` and present the results as a structured breakdown. It will also call `get_cost_by_project` to show per-project spending and identify the most expensive model.

### 5. View Recent Requests and Latency

**Prompt:**

```
Show me the last 20 requests to VoiceGateway. Are there any with high latency or errors?
Flag any requests where TTFB exceeded 500ms.
```

Claude Code will call `get_recent_requests` with `limit=20` and analyze the results, highlighting:
- Requests with `status: "error"`
- Requests where `ttfb_ms > 500`
- Any fallback events (`fallback_from` is not null)

### 6. Audit Configuration Changes

**Prompt:**

```
Show me all configuration changes made to VoiceGateway in the last 24 hours.
Who added or modified providers?
```

Claude Code will call `get_audit_log` and display the history of changes, including:
- What was changed (provider, model, or project)
- What action was taken (create, update, delete)
- The source of the change (api, mcp, dashboard)
- What specifically changed (from the `changes_json` field)

### 7. Delete Unused Resources

**Prompt:**

```
List all models registered in VoiceGateway. Delete the ones that have had zero requests in the last 30 days.
```

Claude Code will:
1. Call `list_models` to get all registered models
2. Call `get_costs` with `period="month"` to check usage
3. Identify models with zero requests
4. Call `delete_model` for each unused model (after confirming with you)

## Available MCP Tools

### Provider Tools

| Tool | Description |
|------|-------------|
| `list_providers` | List all configured providers with masked API keys |
| `get_provider` | Get details for a specific provider |
| `create_provider` | Add a new provider with encrypted API key |
| `delete_provider` | Remove a provider |

### Model Tools

| Tool | Description |
|------|-------------|
| `list_models` | List all registered models across modalities |
| `get_model` | Get details for a specific model |
| `register_model` | Register a new model for a provider |
| `delete_model` | Remove a model registration |

### Project Tools

| Tool | Description |
|------|-------------|
| `list_projects` | List all projects with budget status |
| `get_project` | Get details and today's stats for a project |
| `create_project` | Create a new project with budget settings |
| `delete_project` | Remove a project |

### Observability Tools

| Tool | Description |
|------|-------------|
| `get_costs` | Get cost summary for a time period |
| `get_cost_by_project` | Get costs broken down by project |
| `get_recent_requests` | Get recent request logs |
| `get_latency_stats` | Get latency statistics per model |
| `get_audit_log` | View configuration change history |

## Authentication

The MCP server supports API key authentication. Set the `VOICEGW_MCP_API_KEY` environment variable to require authentication for all MCP tool calls:

```json
{
  "mcpServers": {
    "voicegateway": {
      "command": "voicegw",
      "args": ["mcp"],
      "env": {
        "VOICEGW_CONFIG": "/path/to/voicegw.yaml",
        "VOICEGW_MCP_API_KEY": "your-secret-key"
      }
    }
  }
}
```

## End-to-End Workflow

Here is a complete workflow you can run through Claude Code:

```
1. "Show me what providers are configured in VoiceGateway."
2. "Add an ElevenLabs provider with API key 'xi_abc123'."
3. "Register the eleven_turbo_v2_5 model as a TTS model for ElevenLabs."
4. "Create a project called 'demo' with a $5 daily budget in warn mode."
5. "Show me today's costs and recent requests for the demo project."
6. "Show me the audit log -- what changes did we just make?"
```

Each step uses the MCP tools transparently. All API keys are encrypted at rest, changes are audit-logged, and the Gateway's config is automatically refreshed after each write.
