---
title: voicegw export-costs
description: Export per-request cost line items for a date window as CSV or JSON.
---
Export per-request cost line items for a date window in CSV or JSON. One row per recorded request: open it in a spreadsheet, or feed it into [`voicegw reconcile`](/cli/reconcile) as the VoiceGateway side of a reconciliation. The full workflow is at [Cost reconciliation](/guide/cost-reconciliation).

## Usage

```bash
voicegw export-costs --start <YYYY-MM-DD> --end <YYYY-MM-DD> [OPTIONS]
```

## Options

| Flag | Short | Type | Default | Description |
|---|---|---|---|---|
| `--start` | | string | required | Start date `YYYY-MM-DD`, UTC, inclusive. |
| `--end` | | string | required | End date `YYYY-MM-DD`, UTC, inclusive. |
| `--project` | `-p` | string | `null` | Optional project filter. |
| `--format` | `-f` | string | `csv` | `csv` or `json`. |
| `--output` | `-o` | string | `-` | Output path; `-` writes to stdout. |
| `--config` | `-c` | string | `null` | Path to `voicegw.yaml`. Auto-discovered if omitted. |

`--end` is inclusive of the whole day (advanced one day internally for the exclusive upper bound). The storage backend must be enabled (`cost_tracking.enabled: true`, or `VOICEGW_DB_PATH`/`VOICEGW_DB_URL`); otherwise the command prints an error and exits `1`.

## Output columns

Both formats share this 10-column schema, rows ordered by timestamp ascending:

| Column | Notes |
|---|---|
| `timestamp` | ISO-8601 UTC, e.g. `2026-04-15T09:30:00+00:00`. |
| `project` | Project id. |
| `modality` | `stt`, `llm`, or `tts`. |
| `provider` | e.g. `openai`, `deepgram`, `cartesia`. |
| `model` | Full `provider/model` string. |
| `input_units` | Tokens (LLM input), minutes (STT), or characters (TTS). |
| `output_units` | Tokens (LLM output); `0` for STT/TTS. |
| `calculated_cost_usd` | Fixed-point USD string, priced through `voice-prices`. |
| `pricing_source` | `voice-prices@<version>` for cloud models, `voicegateway-local` for `local/*`/`ollama/*`, empty for unknown. |
| `status` | `ok` or an error tag. |

`--format json` writes JSONL (one object per line, no outer array).

## Examples

```bash
voicegw export-costs --start 2026-05-01 --end 2026-05-31
```

```bash
voicegw export-costs --start 2026-05-01 --end 2026-05-31 --output may-2026.csv
```

Prints `Wrote N record(s) to may-2026.csv` on success.

```bash
voicegw export-costs \
  --start 2026-05-01 --end 2026-05-31 \
  --project production --format json --output may-2026-production.json
```

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | Storage backend not configured. |
| `2` | Bad input: malformed date or unknown `--format`. |

## Related

[`voicegw reconcile`](/cli/reconcile) | [`voicegw costs`](/cli/costs) | [`voicegw logs`](/cli/logs) | [Cost reconciliation](/guide/cost-reconciliation)
