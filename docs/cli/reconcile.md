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
| `--threshold` | | `float` | `5.0` | Flag rows whose absolute cost diff % exceeds this threshold. The default mirrors the v0.0.4 disclosure that LLM estimates can drift up to ~5%. |
| `--config` | `-c` | `string` | `null` | Path to `voicegw.yaml`. Auto-discovered if omitted. |

## Prerequisites

- Cost tracking enabled in `voicegw.yaml` (the command exits with 1 otherwise).
- A provider usage file in VG's canonical schema (one schema per provider; see [Reconcile File Formats](/reference/reconcile-formats)).

## Output

### Text (default)

An aligned table with one row per model. Columns: model, VG units, provider units, units Δ%, VG cost, provider cost, cost Δ$, cost Δ%. Rows whose absolute cost diff % exceeds `--threshold` are tagged with a trailing ` *` and rendered in ANSI yellow when stdout is a TTY (no color when piped or captured). Models present in only one side carry a `(no vg data)` or `(no provider data)` suffix instead.

A Total row sums VG cost and provider cost across rows where both sides matched (missing-side rows are excluded so their `$0` placeholders do not skew the total). When any rows are flagged, a footer line `(N flagged row(s) marked with *)` follows.

The unit label adapts to the provider:

- `tokens` for OpenAI (input + output, summed).
- `audio_s` for Deepgram (seconds; VG-side minutes are converted at the boundary).
- `chars` for Cartesia.

### CSV

Twelve columns: `model, vg_units, provider_units, units_diff_abs, units_diff_pct, vg_cost_usd, provider_cost_usd, cost_diff_abs, cost_diff_pct, matched_in_vg, matched_in_provider, flagged`. The `flagged` column is `True`/`False` so spreadsheets can filter on it without re-deriving the threshold comparison.

### JSON

A nested document matching design §2.2:

```json
{
  "provider": "openai",
  "period": {"start": "2026-05-01", "end": "2026-05-31"},
  "rows": [
    {"model": "gpt-4o-mini", "vg_cost": 0.94, "provider_cost": 1.0, "cost_diff_abs": 0.06, "cost_diff_pct": 6.0, "flagged": true, ...}
  ],
  "total": {"vg_cost": 7.10, "provider_cost": 7.31, "diff_abs": 0.21, "diff_pct": 2.91},
  "flagged_count": 1
}
```

`total` sums only rows where both sides matched (missing-side rows excluded, mirroring the text format). `flagged_count` counts rows where `flagged=True`. Useful for piping into a monitoring or alerting tool.

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
  --format json | jq '.rows[] | select(.cost_diff_pct > 5)'
```

Note the `.rows[]` selector: the JSON output is a nested document
keyed on `rows`, not a top-level array. See the [JSON section
above](#json) for the full schema.

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
