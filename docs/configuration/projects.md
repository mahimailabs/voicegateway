---
title: Projects
description: Per-project cost tracking, budget enforcement, provider key overrides, tags, and guardrail policies for attributing VoiceGateway costs to specific agents, teams, or customers.
---

# Projects

Projects are the primary mechanism for attributing costs to specific agents, teams, or customers. Each project can carry a daily budget, override provider keys, and define guardrail policies.

## Defining projects

Projects live under `projects:` in `voicegw.yaml`. The key is the project ID used everywhere (CLI, API, dashboard).

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
| `description` | string | `""` | Free-text description |
| `daily_budget` | float | `0.0` | Daily spending limit in USD. `0.0` means no limit. |
| `budget_action` | string | `"warn"` | Action when budget is exceeded: `warn`, `throttle`, or `block` |
| `tags` | list of strings | `[]` | Arbitrary tags for filtering and dashboard display |
| `providers` | mapping | `{}` | Per-project provider keys. Overrides the top-level `providers:` block for this project. |
| `default_stack` | string | `""` | Dashboard display hint. See [Stacks](/configuration/stacks). |
| `guardrails` | mapping | all off | Optional per-project LLM guardrail policy. |

## Budget actions

The `budget_action` field controls what happens when a project's daily spend exceeds `daily_budget`:

- `warn`: a warning is logged but requests continue. Use for development or low-risk projects.
- `throttle`: requests are artificially slowed to reduce the consumption rate.
- `block`: requests are rejected until the next calendar day when the budget resets.

```yaml
projects:
  strict-budget:
    name: Strict Budget Project
    daily_budget: 25.00
    budget_action: block
```

<Warning>
Budget enforcement requires `cost_tracking: true` in the `observability` block. If cost tracking is disabled, `budget_action` never triggers because there is no spend data to compare against.
</Warning>

## Active project resolution

The active project for a call resolves in this order:

1. The `tenant_id` carried in the `attach()` call or agent metadata.
2. `VOICEGW_ACTIVE_PROJECT` environment variable.
3. `default_project` in `voicegw.yaml`.
4. The literal `"default"` (auto-created on first run).

See [attach()](/guide/attach) for how to bind a tenant to a call.

## Guardrails

Per-project guardrails are optional policies applied to LLM calls.

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

Supported categories: `pii`, `financial`, `medical`, `prompt_injection`, `off_topic`.
Supported actions: `redact`, `block`, `alert`, `off`.

Use `guard()` in agent code to enforce these policies at the call layer. See [guard()](/guide/guard) for runtime behavior and audit events.

## Querying project data

### From the CLI

```bash
voicegw projects                         # list all projects
voicegw project customer-support         # project details
voicegw costs --project customer-support # costs today
voicegw logs --project customer-support  # recent requests
```

### From the HTTP API

```bash
curl http://localhost:8080/v1/projects
curl http://localhost:8080/v1/costs?project=customer-support
```

### From the dashboard

The web dashboard (`voicegw dashboard`) shows per-project cost breakdowns, daily spend trends, and budget utilization.

## Tags

Tags are arbitrary strings for filtering and visual organization. The dashboard uses the first tag to choose an accent color:

- Tags containing `prod`: green accent
- Tags containing `stag`: yellow accent
- Tags containing `dev` or `test`: blue accent
- All other tags: pink accent

## Runtime project management

Projects can also be created at runtime through the dashboard, the MCP server (`voicegw mcp`), or the HTTP API (`/v1/projects`). Runtime-created projects are persisted in SQLite and merged with YAML-defined projects on startup. YAML-defined projects take precedence on conflicts.

---

See [Stacks](/configuration/stacks) for the `default_stack` field.
See [Observability](/configuration/observability) for `cost_tracking` and budget enforcement.
See [voicegw.yaml reference](/configuration/voicegw-yaml) for the full config file shape.
