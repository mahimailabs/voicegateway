# Projects

Projects provide per-project cost tracking, budget enforcement, and organizational grouping. They are the primary mechanism for attributing costs to specific agents, teams, or customers.

## Defining projects

Projects are defined in `voicegw.yaml` under the `projects` section. The key is the project ID used in code.

```yaml
projects:
  customer-support:
    name: Customer Support Bot
    description: Production customer-facing support agent
    default_stack: premium
    daily_budget: 50.00
    budget_action: throttle
    tags: [prod, support]
  internal-testing:
    name: Internal Testing
    description: QA and development testing
    default_stack: budget
    daily_budget: 10.00
    budget_action: warn
    tags: [dev, qa]
```

## Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | string | required | Human-readable project name |
| `description` | string | `""` | Description of the project's purpose |
| `default_stack` | string | `""` | Name of the stack to use by default for this project |
| `daily_budget` | float | `0.0` | Daily spending limit in USD. `0.0` means no limit. |
| `budget_action` | string | `"warn"` | What to do when budget is exceeded: `warn`, `throttle`, or `block` |
| `tags` | list of strings | `[]` | Arbitrary tags for filtering and dashboard display |

## Budget actions

The `budget_action` field controls what happens when a project's daily spend exceeds its `daily_budget`:

- **`warn`** -- a warning is logged but requests continue normally. Use this for development and low-risk projects where you want visibility without disruption.
- **`throttle`** -- requests are artificially slowed down to reduce consumption rate. Use this when you want to discourage overuse without hard-blocking.
- **`block`** -- requests are rejected entirely until the next day when the budget resets. Use this for strict cost controls on production projects.

```yaml
projects:
  strict-budget:
    name: Strict Budget Project
    daily_budget: 25.00
    budget_action: block
```

## Using projects in code

Pass the `project` argument to any gateway method to attribute requests to a project:

```python
from voicegateway import Gateway

gw = Gateway()

# Attribute costs to the "customer-support" project
stt = gw.stt("deepgram/nova-3", project="customer-support")
llm = gw.llm("anthropic/claude-sonnet-4-20250514", project="customer-support")
tts = gw.tts("cartesia/sonic-3", project="customer-support")

# Use with stacks
stt, llm, tts = gw.stack("premium", project="customer-support")

# Use with fallbacks
stt = gw.stt_with_fallback(project="customer-support")
```

If no `project` is specified, requests are attributed to the `"default"` project.

## Querying project data

### From code

```python
# List all configured projects
projects = gw.list_projects()

# Get cost summary for a specific project
costs = gw.costs(period="today", project="customer-support")
```

### From the CLI

```bash
voicegw projects          # list all projects
voicegw costs --project customer-support
```

### From the dashboard

The web dashboard (`voicegw dashboard`) shows per-project cost breakdowns, daily spend trends, and budget utilization.

## Tags

Tags are arbitrary strings used for filtering and visual organization. The dashboard uses the first tag to determine accent colors:

- Tags containing `prod` render with a green accent
- Tags containing `stag` render with a yellow accent
- Tags containing `dev` or `test` render with a blue accent
- All other tags render with a pink accent

```yaml
projects:
  staging-bot:
    name: Staging Bot
    tags: [staging, v2]
    # Renders with yellow accent in dashboard
```

## Runtime project management

Projects can also be created and managed at runtime through:

- The **dashboard** web UI
- The **MCP server** (`voicegw mcp`)
- The **HTTP API** (`/v1/projects`)

Projects created at runtime are persisted in the SQLite database and merged with YAML-defined projects on startup. YAML-defined projects take precedence if there is a conflict.

See: [Stacks](/configuration/stacks), [Observability](/configuration/observability), [voicegw.yaml Reference](/configuration/voicegw-yaml)
