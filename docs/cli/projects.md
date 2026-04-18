# voicegw projects / voicegw project

List all configured projects or show details for a single project.

## Purpose

The `projects` command lists every project defined in the gateway configuration, showing their name, tags, daily budget, and default stack. The `project` command shows full details for a single project, including today's spend if cost tracking is enabled.

## Syntax

```bash
# List all projects
voicegw projects [OPTIONS]

# Show a single project
voicegw project <PROJECT_ID> [OPTIONS]
```

## Options (projects)

| Flag | Short | Type | Default | Description |
|---|---|---|---|---|
| `--config` | `-c` | `string` | `null` | Path to `voicegw.yaml`. Auto-discovered if omitted. |

## Options (project) {#project-detail}

| Argument | Type | Required | Description |
|---|---|---|---|
| `PROJECT_ID` | `string` | yes | The project ID to display. |

| Flag | Short | Type | Default | Description |
|---|---|---|---|---|
| `--config` | `-c` | `string` | `null` | Path to `voicegw.yaml`. Auto-discovered if omitted. |

## Output (projects)

A table with columns:

| Column | Description |
|---|---|
| **ID** | Project identifier (e.g., `tonys-pizza`). |
| **Name** | Human-readable name. |
| **Tags** | Space-separated tags. |
| **Budget/day** | Daily budget in USD, or `-` if unlimited. |
| **Default Stack** | Named stack, or `-` if none. |

## Output (project)

A Rich panel showing:

- Project name and description.
- Tags, default stack, and daily budget.
- Today's spend and request count (if cost tracking is enabled).

## Examples

### List all projects

```bash
voicegw projects
```

```
                    Projects
┌──────────────┬──────────────┬────────────┬────────────┬───────────────┐
│ ID           │ Name         │ Tags       │ Budget/day │ Default Stack │
├──────────────┼──────────────┼────────────┼────────────┼───────────────┤
│ tonys-pizza  │ Tony's Pizza │ production │ $10.00     │ premium       │
│ sushi-bot    │ Sushi Bot    │ staging    │ $5.00      │ budget        │
│ dev-sandbox  │ Dev Sandbox  │ dev        │ $1.00      │ local         │
└──────────────┴──────────────┴────────────┴────────────┴───────────────┘
```

### Show details for a single project

```bash
voicegw project tonys-pizza
```

```
╭─ Project: tonys-pizza ───────────────────╮
│ Tony's Pizza                              │
│ Pizza ordering voice agent                │
│                                           │
│ Tags: production                          │
│ Default Stack: premium                    │
│ Daily Budget: $10.00                      │
╰───────────────────────────────────────────╯

Today: $2.4500 (120 requests)
```

### List projects with a custom config

```bash
voicegw projects -c /etc/voicegateway/voicegw.yaml
```

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Success (including when no projects are configured -- prints a warning). |
| `1` | Config failed to load, or the specified project ID was not found. |

## Related Commands

- [`voicegw costs`](/cli/costs) -- view cost breakdown for a project
- [`voicegw logs`](/cli/logs) -- view request logs filtered by project
- [`voicegw status`](/cli/status) -- see which providers each project uses
