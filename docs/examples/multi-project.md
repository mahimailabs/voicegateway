# Multi-Project Setup

Configure multiple projects with different model stacks, budgets, and tracking. This is useful when you have separate teams, environments, or products sharing a single VoiceGateway instance.

## Configuration

```yaml
projects:
  prod:
    name: Production
    description: Customer-facing voice agents
    daily_budget: 50.00
    budget_action: throttle
    tags: [production, customer-facing]
    providers:
      openai:
        api_key: ${PROD_OPENAI_KEY}
      deepgram:
        api_key: ${PROD_DEEPGRAM_KEY}
      cartesia:
        api_key: ${PROD_CARTESIA_KEY}

  staging:
    name: Staging
    description: Pre-release testing environment
    daily_budget: 10.00
    budget_action: warn
    tags: [staging, testing]
    providers:
      openai:
        api_key: ${STAGING_OPENAI_KEY}
      deepgram:
        api_key: ${STAGING_DEEPGRAM_KEY}
      cartesia:
        api_key: ${STAGING_CARTESIA_KEY}

  dev:
    name: Development
    description: Developer sandbox
    daily_budget: 5.00
    budget_action: block
    tags: [development]
    # dev uses local providers — no api_key needed; ollama and friends
    # read from the top-level providers: block.

providers:
  ollama:
    base_url: http://localhost:11434
  whisper: {}
  kokoro: {}

cost_tracking:
  enabled: true
```

## Using Projects in Code

```python
from voicegateway import inference

# Production: pass the project explicitly per call context.
inference.set_project("prod")
stt = inference.STT("deepgram/nova-3")
llm = inference.LLM("openai/gpt-4.1-mini")
tts = inference.TTS("cartesia/sonic-3")
```

The active project is scoped to the current async context, so different
asyncio tasks within the same process can each pick a different
project without interfering:

```python
async def production_handler(ctx):
    inference.set_project("prod")
    # all inference factories below charge prod
    ...

async def staging_handler(ctx):
    inference.set_project("staging")
    # sibling task — no leakage
    ...

async def dev_handler(ctx):
    inference.set_project("dev")
    stt = inference.STT("local/whisper-large-v3")
    llm = inference.LLM("ollama/qwen2.5:3b")
    tts = inference.TTS("local/kokoro")
```

For workers that always serve one project, set `default_project: prod` (or whichever) in `voicegw.yaml` and skip the `set_project` call entirely.

## Querying Per-Project Costs

### Via the CLI

```bash
voicegw costs --project prod
voicegw costs --project staging
voicegw projects   # shows budget + recent spend per project
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
