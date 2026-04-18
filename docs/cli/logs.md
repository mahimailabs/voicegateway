# voicegw logs

Show recent request logs from the gateway's SQLite database.

## Purpose

The `logs` command displays a table of recent gateway requests, including the timestamp, project, modality, model, cost, latency, and status. Use it to debug request flow, investigate errors, or monitor activity in real time.

## Syntax

```bash
voicegw logs [OPTIONS]
```

## Options

| Flag | Short | Type | Default | Description |
|---|---|---|---|---|
| `--config` | `-c` | `string` | `null` | Path to `voicegw.yaml`. Auto-discovered if omitted. |
| `--project` | `-p` | `string` | `null` | Filter logs to a specific project ID. |
| `--tail` | `-n` | `integer` | `20` | Number of rows to display. |
| `--modality` | `-m` | `string` | `null` | Filter by modality: `stt`, `llm`, or `tts`. |

## Prerequisites

Cost tracking must be enabled in `voicegw.yaml` for logs to be recorded. If disabled, the command prints a warning and exits.

## Output

A table with columns:

| Column | Description |
|---|---|
| **Time** | Request timestamp in `HH:MM:SS` format. |
| **Project** | Project ID, or `-` if untagged. |
| **Modality** | `STT`, `LLM`, or `TTS`. |
| **Model** | Full model ID (e.g., `deepgram/nova-3`). |
| **Cost** | Cost in USD with 6 decimal places. |
| **Latency** | Total latency in milliseconds. |
| **Status** | `success`, `error`, or `fallback`. |

## Examples

### Show the last 20 requests

```bash
voicegw logs
```

```
              Recent Requests (20)
┌──────────┬─────────────┬──────────┬────────────────────┬───────────┬─────────┬─────────┐
│ Time     │ Project     │ Modality │ Model              │ Cost      │ Latency │ Status  │
├──────────┼─────────────┼──────────┼────────────────────┼───────────┼─────────┼─────────┤
│ 14:23:01 │ tonys-pizza │ STT      │ deepgram/nova-3    │ $0.012000 │ 142ms   │ success │
│ 14:23:02 │ tonys-pizza │ LLM      │ openai/gpt-4o-mini │ $0.003200 │ 890ms   │ success │
│ 14:23:03 │ tonys-pizza │ TTS      │ cartesia/sonic-3   │ $0.008500 │ 210ms   │ success │
└──────────┴─────────────┴──────────┴────────────────────┴───────────┴─────────┴─────────┘
```

### Show the last 50 STT requests

```bash
voicegw logs --tail 50 --modality stt
```

### Filter by project

```bash
voicegw logs --project tonys-pizza -n 100
```

### Combine filters

```bash
voicegw logs -p tonys-pizza -m llm -n 10
```

Shows the last 10 LLM requests for the `tonys-pizza` project.

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Success (including when no logs are found). |
| `1` | Config failed to load. |

## Related Commands

- [`voicegw costs`](/cli/costs) -- aggregated cost view instead of individual records
- [`voicegw status`](/cli/status) -- check which providers are active
- [`voicegw projects`](/cli/projects) -- find project IDs to filter on
