---
title: voicegw logs
description: Show recent request logs from the gateway's SQLite database, filterable by project and modality.
---
Show recent request logs from the gateway's SQLite database. Use it to debug request flow, investigate errors, or watch activity in near real time.

## Usage

```bash
voicegw logs [OPTIONS]
```

## Options

| Flag | Short | Type | Default | Description |
|---|---|---|---|---|
| `--config` | `-c` | string | `null` | Path to `voicegw.yaml`. Auto-discovered if omitted. |
| `--project` | `-p` | string | `null` | Filter to a specific project id. |
| `--tail` | `-n` | integer | `20` | Number of rows to show. |
| `--modality` | `-m` | string | `null` | Filter by `stt`, `llm`, or `tts`. |

Cost tracking must be enabled in `voicegw.yaml` for requests to be logged. If it isn't, the command prints a warning and exits `0`.

## Output

| Column | Description |
|---|---|
| **Time** | Request timestamp, `HH:MM:SS`. |
| **Project** | Project id, or `-` if untagged. |
| **Modality** | `STT`, `LLM`, or `TTS`. |
| **Model** | Full model id, e.g. `deepgram/nova-3`. |
| **Cost** | USD, 6 decimal places. |
| **Latency** | Total latency in ms. |
| **Status** | `success`, `error`, or `fallback`. |

## Examples

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

```bash
voicegw logs --tail 50 --modality stt
voicegw logs --project tonys-pizza -n 100
voicegw logs -p tonys-pizza -m llm -n 10
```

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success, including when no logs are found. |
| `1` | Config failed to load. |

## Related

[`voicegw costs`](/cli/costs) | [`voicegw export-costs`](/cli/export-costs) | [Projects](/configuration/projects)
