---
title: voicegw costs
description: Display a cost summary from the gateway's request log, broken down by provider and model.
---
Display a cost summary from the gateway's request log.

## Usage

```bash
voicegw costs [OPTIONS]
```

## Options

| Flag | Short | Type | Default | Description |
|---|---|---|---|---|
| `--config` | `-c` | string | `null` | Path to `voicegw.yaml`. Auto-discovered if omitted. |
| `--project` | `-p` | string | `null` | Filter to a specific project id. |
| `--week` | | boolean | `false` | Show the weekly summary instead of today. |
| `--month` | | boolean | `false` | Show the monthly summary instead of today. |

If both `--week` and `--month` are omitted, the period is `today`. If both are passed, `--month` wins.

## Prerequisites

Cost tracking must be enabled in `voicegw.yaml`:

```yaml
cost_tracking:
  enabled: true
  db_path: ~/.config/voicegateway/voicegw.db
```

If it isn't, the command prints a warning and exits `0`.

## Output

- A header with the period and, if passed, the project filter.
- **Total** in USD.
- A **By Provider** table (cost, request count).
- A **By Model** table (cost, request count).
- A footer line naming the pricing source in effect for each modality (e.g. `LLM: voice-prices@0.1.0`), and a reminder to run `voicegw reconcile` to verify against a provider invoice.

If nothing has been recorded, it prints "No requests recorded yet."

## Examples

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

Pricing sources: LLM: voice-prices@0.1.0 | STT: voice-prices@0.1.0 | TTS: voice-prices@0.1.0
Costs are estimates. Run `voicegw reconcile --provider <name> --provider-usage-file <file>` to verify against your provider invoice.
```

```bash
voicegw costs --week --project tonys-pizza
voicegw costs --month
voicegw costs -c /etc/voicegateway/voicegw.yaml --week
```

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success, including when cost tracking is disabled (prints a warning). |
| `1` | Config failed to load. |

## Related

[`voicegw reconcile`](/cli/reconcile) | [`voicegw logs`](/cli/logs) | [`voicegw export-costs`](/cli/export-costs) | [Projects](/configuration/projects)
