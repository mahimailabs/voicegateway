# voicegw reconcile

Diff VoiceGateway's recorded costs against a provider's usage export.

## Purpose

The `reconcile` command reads VG's per-request log records for a date window, parses an operator-supplied normalized provider usage file, and produces a per-model diff with absolute and percent differences. The full workflow (when to reconcile, how to interpret the diff, expected drift per modality) lives at [Cost Reconciliation](/guide/cost-reconciliation).

The provider-side input file format is documented per provider at [Reconcile File Formats](/reference/reconcile-formats).

## Syntax

```bash
voicegw reconcile --provider <name> --start <YYYY-MM-DD> --end <YYYY-MM-DD> \
  --provider-usage-file <PATH> [OPTIONS]
```

## Options

| Flag | Short | Type | Default | Description |
|---|---|---|---|---|
| `--provider` | | `string` | required | Provider name: `openai`, `deepgram`, or `cartesia`. |
| `--start` | | `string` | required | Start date in `YYYY-MM-DD` (UTC, inclusive). |
| `--end` | | `string` | required | End date in `YYYY-MM-DD` (UTC, inclusive day). |
| `--provider-usage-file` | | `string` | required | Path to the provider's normalized usage file (`.csv` or `.json`). |
| `--format` | `-f` | `string` | `text` | Output format: `text`, `csv`, or `json`. |
| `--config` | `-c` | `string` | `null` | Path to `voicegw.yaml`. Auto-discovered if omitted. |

## Prerequisites

- Cost tracking enabled in `voicegw.yaml` (the command exits with 1 otherwise).
- A provider usage file in VG's canonical schema (one schema per provider; see [Reconcile File Formats](/reference/reconcile-formats)).

## Output

### Text (default)

An aligned table with one row per model. Columns: model, VG units, provider units, units Δ%, VG cost, provider cost, cost Δ$, cost Δ%. Models present in only one side carry a `(vg-missing)` or `(prov-missing)` flag.

The unit label adapts to the provider:

- `tokens` for OpenAI (input + output, summed).
- `audio_s` for Deepgram (seconds; VG-side minutes are converted at the boundary).
- `chars` for Cartesia.

### CSV

Eleven columns: `model, vg_units, provider_units, units_diff_abs, units_diff_pct, vg_cost_usd, provider_cost_usd, cost_diff_abs, cost_diff_pct, matched_in_vg, matched_in_provider`.

### JSON

A list of dicts with the same fields as the CSV. Useful for piping into a monitoring or alerting tool.

## Examples

### Diff May 2026 OpenAI usage

```bash
voicegw reconcile \
  --provider openai \
  --start 2026-05-01 --end 2026-05-31 \
  --provider-usage-file openai-may-2026.csv
```

### Same window as JSON for piping

```bash
voicegw reconcile \
  --provider deepgram \
  --start 2026-05-01 --end 2026-05-31 \
  --provider-usage-file deepgram-may-2026.csv \
  --format json | jq '.[] | select(.cost_diff_pct > 5)'
```

### Cartesia with the JSON variant of the canonical file

```bash
voicegw reconcile \
  --provider cartesia \
  --start 2026-05-01 --end 2026-05-31 \
  --provider-usage-file cartesia-may-2026.json
```

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | Cost tracking is not enabled. |
| `2` | Bad input: unsupported provider, malformed date, missing usage file, parse error, or unknown format. |

## Related commands

- [`voicegw export-costs`](/cli/export-costs) -- inspect VG's per-request rows directly.
- [`voicegw costs`](/cli/costs) -- aggregated summary by provider/model.

## See also

- [Cost Reconciliation](/guide/cost-reconciliation) -- when to reconcile, how to interpret the diff, per-modality drift tolerance.
- [Reconcile File Formats](/reference/reconcile-formats) -- per-provider schemas this command expects.
