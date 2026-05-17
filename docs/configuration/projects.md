# Projects

Projects provide per-project cost tracking, budget enforcement, and organizational grouping. They are the primary mechanism for attributing costs to specific agents, teams, or customers.

## Defining projects

Projects are defined in `voicegw.yaml` under the `projects` section. The key is the project ID used in code.

```yaml
projects:
  customer-support:
    name: Customer Support Bot
    description: Production customer-facing support agent
    daily_budget: 50.00
    budget_action: throttle
    tags: [prod, support]
    providers:
      deepgram:
        api_key: ${SUPPORT_DEEPGRAM_KEY}
      openai:
        api_key: ${SUPPORT_OPENAI_KEY}
      cartesia:
        api_key: ${SUPPORT_CARTESIA_KEY}
  internal-testing:
    name: Internal Testing
    description: QA and development testing
    daily_budget: 10.00
    budget_action: warn
    tags: [dev, qa]
    providers:
      openai:
        api_key: ${TEST_OPENAI_KEY}

default_project: customer-support
```

## Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | string | required | Human-readable project name |
| `description` | string | `""` | Description of the project's purpose |
| `daily_budget` | float | `0.0` | Daily spending limit in USD. `0.0` means no limit. |
| `budget_action` | string | `"warn"` | What to do when budget is exceeded: `warn`, `throttle`, or `block` |
| `tags` | list of strings | `[]` | Arbitrary tags for filtering and dashboard display |
| `providers` | mapping | `{}` | Per-project provider keys. Wins over the top-level `providers:` block when set. |
| `default_stack` | string | `""` | Optional dashboard / display hint. Not used by the inference module. |
| `guardrails` | mapping | all categories `off` | Optional LLM-side guardrail policy. |

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

## Selecting a project from code

The active project resolves in this order:

1. `inference.set_project(name)` in the current async context.
2. `VOICEGW_ACTIVE_PROJECT` environment variable.
3. `default_project` field in `voicegw.yaml`.
4. The literal `"default"` (auto-created on first run).

```python
from voicegateway import inference

# Either rely on default_project: customer-support in voicegw.yaml,
# or pick the project explicitly per call context:
inference.set_project("customer-support")

stt = inference.STT("deepgram/nova-3")
llm = inference.LLM("anthropic/claude-sonnet-4-20250514")
tts = inference.TTS("cartesia/sonic-3")
```

`inference.set_project` is scoped to the current `asyncio` context; sibling tasks each manage their own project state without leaking.

## Guardrails

Guardrails are optional per-project policies injected into `voicegateway.inference.LLM.chat(...)`.

```yaml
projects:
  customer-support:
    name: Customer Support Bot
    guardrails:
      enabled: true
      categories:
        pii: redact
        financial: block
        medical: alert
        prompt_injection: block
        off_topic: off
```

Supported categories are `pii`, `financial`, `medical`, `prompt_injection`, and `off_topic`. Supported actions are `redact`, `block`, `alert`, and `off`. See [Voice-specific guardrails](/guide/guardrails) for runtime behavior, bypass, and audit events.

## Querying project data

### From the CLI

```bash
voicegw projects                         # list all projects
voicegw project customer-support         # project details
voicegw costs --project customer-support # project costs today
voicegw logs --project customer-support  # recent requests for the project
```

### From the HTTP API

```bash
curl http://localhost:8080/v1/projects
curl http://localhost:8080/v1/costs?project=customer-support
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
