# Multi-Project Setup

Configure multiple projects with different model stacks, budgets, and tracking. This is useful when you have separate teams, environments, or products sharing a single VoiceGateway instance.

## Configuration

```yaml
providers:
  openai:
    api_key: ${OPENAI_API_KEY}
  deepgram:
    api_key: ${DEEPGRAM_API_KEY}
  cartesia:
    api_key: ${CARTESIA_API_KEY}
  groq:
    api_key: ${GROQ_API_KEY}
  ollama:
    base_url: http://localhost:11434

models:
  stt:
    deepgram/nova-3:
      provider: deepgram
      model: nova-3
    whisper/large-v3:
      provider: whisper
      model: large-v3
  llm:
    openai/gpt-4.1-mini:
      provider: openai
      model: gpt-4.1-mini
    groq/llama-3.3-70b-versatile:
      provider: groq
      model: llama-3.3-70b-versatile
    ollama/qwen2.5:3b:
      provider: ollama
      model: qwen2.5:3b
  tts:
    cartesia/sonic-3:
      provider: cartesia
      model: sonic-3
      default_voice: 794f9389-aac1-45b6-b726-9d9369183238
    kokoro/default:
      provider: kokoro
      model: default

stacks:
  premium:
    stt: deepgram/nova-3
    llm: openai/gpt-4.1-mini
    tts: cartesia/sonic-3
  budget:
    stt: deepgram/nova-3
    llm: groq/llama-3.3-70b-versatile
    tts: cartesia/sonic-3
  local:
    stt: whisper/large-v3
    llm: ollama/qwen2.5:3b
    tts: kokoro/default

projects:
  prod:
    name: Production
    description: Customer-facing voice agents
    daily_budget: 50.00
    budget_action: throttle
    default_stack: premium
    tags: [production, customer-facing]

  staging:
    name: Staging
    description: Pre-release testing environment
    daily_budget: 10.00
    budget_action: warn
    default_stack: budget
    tags: [staging, testing]

  dev:
    name: Development
    description: Developer sandbox
    daily_budget: 5.00
    budget_action: block
    default_stack: local
    tags: [development]

cost_tracking:
  enabled: true
```

## Using Projects in Code

```python
from voicegateway import Gateway

gw = Gateway()

# Production: uses premium stack, throttles to local on budget exceed
stt, llm, tts = gw.stack("premium", project="prod")

# Staging: uses budget stack, warns on budget exceed
stt, llm, tts = gw.stack("budget", project="staging")

# Development: uses local stack, blocks on budget exceed
stt, llm, tts = gw.stack("local", project="dev")
```

### Per-Request Project Tagging

You can also tag individual requests with a project ID:

```python
# These requests are tracked under the "prod" project
stt = gw.stt("deepgram/nova-3", project="prod")
llm = gw.llm("openai/gpt-4.1-mini", project="prod")
tts = gw.tts("cartesia/sonic-3", project="prod")

# These are tracked under "staging"
stt = gw.stt("deepgram/nova-3", project="staging")
llm = gw.llm("groq/llama-3.3-70b-versatile", project="staging")
```

## Querying Per-Project Costs

```python
# Get costs for a specific project
prod_costs = gw.costs(period="today", project="prod")
print(f"Production today: ${prod_costs['total']:.2f}")

staging_costs = gw.costs(period="today", project="staging")
print(f"Staging today: ${staging_costs['total']:.2f}")

# Get all projects and their status
projects = gw.list_projects()
for p in projects:
    print(f"  {p['id']}: {p['name']} (budget: ${p['daily_budget']:.2f}/day)")
```

### Via the HTTP API

```bash
# Per-project cost breakdown
curl http://localhost:8080/v1/costs?period=today&project=prod

# All projects
curl http://localhost:8080/v1/projects

# Project-level request logs
curl http://localhost:8080/v1/logs?project=prod&limit=50
```

## Project Accent Colors

The dashboard assigns accent colors based on the project's first tag:

| Tag Contains | Color |
|-------------|-------|
| `prod` | Green |
| `stag` | Yellow |
| `dev` or `test` | Blue |
| (anything else) | Pink |

This makes it easy to visually distinguish environments at a glance.

## Budget Behavior by Project

| Project | Budget | Action | What Happens When Exceeded |
|---------|--------|--------|---------------------------|
| prod | $50/day | `throttle` | Raises `BudgetThrottleSignal` -- app falls back to local models |
| staging | $10/day | `warn` | Logs a warning, request proceeds normally |
| dev | $5/day | `block` | Raises `BudgetExceededError` -- request is rejected |

## Dynamic Project Management

Projects can also be created and updated at runtime through the dashboard or MCP server, without editing `voicegw.yaml`:

```bash
# Via the HTTP API
curl -X POST http://localhost:8080/v1/projects \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "demo",
    "name": "Demo Environment",
    "daily_budget": 2.00,
    "budget_action": "warn",
    "tags": ["demo"]
  }'
```

These dynamically created projects are stored in the `managed_projects` SQLite table and merged with YAML-defined projects at startup and after each write.

## SQL Views for Reporting

The `project_daily_costs` view aggregates costs by project and day:

```sql
SELECT project, day, SUM(total_cost) as cost
FROM project_daily_costs
WHERE day >= date('now', '-7 days')
GROUP BY project, day
ORDER BY project, day;
```

This is what the dashboard uses to render per-project cost charts.
