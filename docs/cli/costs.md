# voicegw costs

Display cost summaries from the gateway's request log.

## Purpose

The `costs` command queries the SQLite database to show how much you have spent on voice AI requests. It breaks costs down by provider and by model, and can be filtered by project and time period.

## Syntax

```bash
voicegw costs [OPTIONS]
```

## Options

| Flag | Short | Type | Default | Description |
|---|---|---|---|---|
| `--config` | `-c` | `string` | `null` | Path to `voicegw.yaml`. Auto-discovered if omitted. |
| `--project` | `-p` | `string` | `null` | Filter costs to a specific project ID. |
| `--week` | | `boolean` | `false` | Show the weekly summary instead of today. |
| `--month` | | `boolean` | `false` | Show the monthly summary instead of today. |

When both `--week` and `--month` are omitted, the default period is `today`. If both are provided, `--month` takes precedence.

## Prerequisites

Cost tracking must be enabled in `voicegw.yaml`:

```yaml
cost_tracking:
  enabled: true
  db_path: ~/.config/voicegateway/voicegw.db
```

If cost tracking is disabled, the command prints a warning and exits.

## Output

The command displays:

1. A header with the period and optional project filter.
2. The total cost in USD.
3. A **By Provider** table with cost and request count per provider.
4. A **By Model** table with cost and request count per model.

If no requests have been recorded, it prints "No requests recorded yet."

## Examples

### Show today's costs

```bash
voicegw costs
```

```
Cost Summary (today)
Total: $1.2345

       By Provider
┌──────────┬─────────┬──────────┐
│ Provider │ Cost    │ Requests │
├──────────┼─────────┼──────────┤
│ deepgram │ $0.5123 │ 42       │
│ openai   │ $0.7222 │ 18       │
└──────────┴─────────┴──────────┘
```

### Show weekly costs for a project

```bash
voicegw costs --week --project tonys-pizza
```

### Show monthly costs

```bash
voicegw costs --month
```

### Use a custom config path

```bash
voicegw costs -c /etc/voicegateway/voicegw.yaml --week
```

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Success (including when cost tracking is disabled -- prints warning). |
| `1` | Config failed to load. |

## Related Commands

- [`voicegw status`](/cli/status) -- see which providers are configured
- [`voicegw logs`](/cli/logs) -- view individual request records
- [`voicegw projects`](/cli/projects) -- list projects with budget info
