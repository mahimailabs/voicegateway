---
title: Projects
description: Define projects in voicegw.yaml for per-agent, per-team, or per-customer cost attribution, and read them back from the CLI, HTTP API, and dashboard.
---
A project attributes cost to one agent, team, or customer. Every request `attach()` or `guard()` records carries a project id; querying costs, logs, or exports by project is how you split a shared deployment's bill.

## Defining projects

Projects live under `projects:` in `voicegw.yaml`. The key is the project id used everywhere (CLI, API, dashboard, and the `project=` argument to `attach()`/`guard()`).

```yaml
projects:
  customer-support:
    name: Customer Support Bot
    description: Production customer-facing support agent
    daily_budget: 50.00
    budget_action: warn
    tags: [prod, support]
    providers:
      deepgram:
        api_key: ${SUPPORT_DEEPGRAM_KEY}
      openai:
        api_key: ${SUPPORT_OPENAI_KEY}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | string | required | Human-readable project name |
| `description` | string | `""` | Free-text description |
| `daily_budget` | float | `0.0` | Daily spend used for the budget status badge. `0` means no cap tracked. |
| `budget_action` | string | `"warn"` | One of `warn`, `throttle`, `block`. See [Budgets](#budgets) below: it's currently a label, not an enforcement switch. |
| `tags` | list of strings | `[]` | Arbitrary tags for filtering and dashboard accent color |
| `providers` | mapping | `{}` | Per-project provider keys, overriding the top-level `providers:` block for this project |
| `default_stack` | string | `""` | Dashboard display hint. See [Models](/configuration/models). |

Projects also accept `routing`, `branding`, `replay`, and `metrics` blocks; those aren't cost-tracking concerns and are covered in the [voicegw.yaml reference](/configuration/voicegw-yaml).

Unknown keys under a project fail config validation (typos are caught at startup, not silently ignored).

## Budgets

`daily_budget` is a dollar cap; `budget_action` (`warn` / `throttle` / `block`) records what you *want* to happen once spend crosses it.

<Warning>
Today none of the three `budget_action` values change gateway behavior on their own. `attach()` only records spend against the cap. The dashboard, `voicegw project <id>`, and the MCP project tools read that spend back as a status of `ok`, `warning` (≥80% of `daily_budget`), or `exceeded` (≥100%): identical regardless of which action is configured. No call is slowed, rerouted, or rejected by `budget_action` alone.
</Warning>

To actually stop or reroute a call once a cap is hit, wrap the provider with [`guard()`](/guide/guard) and pass its own `budget="$X/day"` argument. That check is a separate mechanism scoped to the `guard()` call itself; it does not read a project's `daily_budget`/`budget_action`.

Budget tracking (the spend that powers the status badge) requires the storage backend to be on: top-level `cost_tracking.enabled: true` in `voicegw.yaml`, or `VOICEGW_DB_PATH` / `VOICEGW_DB_URL` set. Without storage, spend always reads as `$0`.

## Which project a call lands under

Pass `project="<id>"` to `attach()` or `guard()` to tag every record from that session. Omit it and records land under the `default` project (auto-created on first run, `$0` budget). See [attach()](/guide/attach) for the exact resolution order between the argument and the `VOICEGW_PROJECT` environment variable.

## Reading project data

### CLI

```bash
voicegw projects                         # list all projects
voicegw project customer-support         # one project's details + today's spend
```

`voicegw projects` prints a table: **ID**, **Name**, **Tags**, **Budget/day** (`-` if unlimited), **Default Stack** (`-` if none). With no projects configured it warns and exits `0`.

`voicegw project <id>` prints a panel with the name, description, tags, default stack, and daily budget, plus a `Today: $X.XXXX (N requests)` line when the storage backend is enabled. Exits `1` if the id isn't found.

Both accept `--config`/`-c` to point at a non-default `voicegw.yaml`.

### HTTP API and dashboard

```bash
curl http://localhost:8080/v1/projects
curl "http://localhost:8080/v1/costs?project=customer-support"
```

The web dashboard (`voicegw dashboard`) shows a per-project spend bar against `daily_budget` and the `budget_action` value as a label. Projects can also be created at runtime from the dashboard, the MCP server (`voicegw mcp`), or `POST /v1/projects`; runtime-created projects persist in SQLite and merge with YAML on startup (YAML wins on id conflicts).

## Tags

Tags are arbitrary strings for filtering. The dashboard picks an accent color from the first tag (substring match, case-insensitive): contains `prod` → green, contains `stag` → yellow, contains `dev` or `test` → blue, anything else → pink.

---

See [Models](/configuration/models) for the `provider/model` id format and the `default_stack` bundles.
See [Tenant attribution](/guide/multi-tenant-quickstart) for per-end-user attribution *within* a project.
See [voicegw.yaml reference](/configuration/voicegw-yaml) for the full config file shape.
