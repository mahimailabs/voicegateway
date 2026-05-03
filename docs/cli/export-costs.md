# voicegw export-costs

Export per-request cost line items for a date window in CSV or JSON.

## Purpose

The `export-costs` command writes one row per recorded request, suitable for spreadsheet review or as the input to a downstream reconciliation step. Pair with [`voicegw reconcile`](/cli/reconcile) to compare the exported numbers against a provider's invoice. The full reconciliation workflow lives at [Cost Reconciliation](/guide/cost-reconciliation).

## Syntax

```bash
voicegw export-costs --start <YYYY-MM-DD> --end <YYYY-MM-DD> [OPTIONS]
```

## Options

| Flag | Short | Type | Default | Description |
|---|---|---|---|---|
| `--start` | | `string` | required | Start date in `YYYY-MM-DD` (UTC, inclusive). |
| `--end` | | `string` | required | End date in `YYYY-MM-DD` (UTC, inclusive day; advanced one day internally for the exclusive upper bound). |
| `--project` | `-p` | `string` | `null` | Optional project filter. |
| `--format` | `-f` | `string` | `csv` | Output format: `csv` or `json`. |
| `--output` | `-o` | `string` | `-` | Output path; `-` writes to stdout. |
| `--config` | `-c` | `string` | `null` | Path to `voicegw.yaml`. Auto-discovered if omitted. |

## Prerequisites

Cost tracking must be enabled in `voicegw.yaml`. If storage is not configured, the command prints a warning and exits with code 1.

## Output columns

The CSV header (and JSON keys) are:

| Column | Type | Notes |
|---|---|---|
| `timestamp` | float | UNIX timestamp the request was recorded. |
| `project` | string | Project ID; `default` if no project routing. |
| `modality` | string | `stt`, `llm`, or `tts`. |
| `provider` | string | e.g., `openai`, `deepgram`, `cartesia`. |
| `model_id` | string | Full `provider/model` string. |
| `input_units` | float | Modality-dependent: tokens (LLM input), minutes (STT), characters (TTS). |
| `output_units` | float | Tokens (LLM output); 0 for STT and TTS. |
| `cost_usd` | float | Calculated cost from VG's pricing catalog. |
| `pricing_source` | string | The catalog that priced this record (e.g., `genai-prices@0.0.57`). |
| `status` | string | `ok` or an error tag from the wrapper. |

Rows are ordered by timestamp ascending.

## Examples

### Export May 2026 to stdout as CSV

```bash
voicegw export-costs --start 2026-05-01 --end 2026-05-31
```

### Export to a file

```bash
voicegw export-costs --start 2026-05-01 --end 2026-05-31 --output may-2026.csv
```

A green confirmation line prints to stdout: `Wrote N record(s) to may-2026.csv`.

### Export as JSON for one project

```bash
voicegw export-costs \
  --start 2026-05-01 --end 2026-05-31 \
  --project production \
  --format json \
  --output may-2026-production.json
```

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | Cost tracking is not enabled. |
| `2` | Bad input: malformed date or unknown format. |

## Related commands

- [`voicegw reconcile`](/cli/reconcile) -- diff the exported numbers against a provider's usage file.
- [`voicegw costs`](/cli/costs) -- aggregated summary instead of per-request rows.
- [`voicegw logs`](/cli/logs) -- recent rows for terminal display.

## See also

- [Cost Reconciliation](/guide/cost-reconciliation) -- the full reconciliation workflow.
- [Reconcile File Formats](/reference/reconcile-formats) -- per-provider schemas the reconcile step expects.
