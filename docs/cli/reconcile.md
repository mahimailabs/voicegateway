---
title: voicegw reconcile
description: Diff VoiceGateway's recorded costs against a provider's usage export.
---
Diff VoiceGateway's recorded costs against a provider's usage export.

## Synopsis

`voicegw reconcile` reads VG's per-request log records for a date window, parses an operator-supplied normalized provider usage file, and produces a per-model diff with absolute and percent differences. The full workflow (when to reconcile, how to interpret the diff, expected drift per modality) lives at [Cost Reconciliation](/guide/cost-reconciliation).

The provider-side input file format is documented per provider at [Reconcile File Formats](/reference/reconcile-formats).

## Usage

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
| `--threshold` | | `float` | `5.0` | Flag rows whose absolute cost diff % exceeds this threshold. The default reflects that LLM estimates can drift up to ~5%. |
| `--config` | `-c` | `string` | `null` | Path to `voicegw.yaml`. Auto-discovered if omitted. |

## Prerequisites

- Cost tracking enabled in `voicegw.yaml` (the command exits with 1 otherwise).
- A provider usage file in VG's canonical schema (one schema per provider; see [Reconcile File Formats](/reference/reconcile-formats)).

## Output

### Text (default)

An aligned table with one row per model. Columns: model, VG units, provider units, units delta%, VG cost, provider cost, cost delta$, cost delta%. Rows whose absolute cost diff % exceeds `--threshold` are tagged with a trailing ` *` and rendered in ANSI yellow when stdout is a TTY (no color when piped or captured). Models present in only one side carry a `(no vg data)` or `(no provider data)` suffix instead.

A Total row sums VG cost and provider cost across rows where both sides matched (missing-side rows are excluded so their `$0` placeholders do not skew the total). When any rows are flagged, a footer line `(N flagged row(s) marked with *)` follows, and then a **What to check** section naming what each disagreement is about.

### What to check

A diff on its own says something is wrong, not what. That matters most when rates are operator-entered: a rule typed as `0.008` instead of `0.08` produces a perfectly plausible bill, and the provider invoice is the only thing in the world that can catch it.

Each flagged row is diagnosed as one of three causes, because they have three different fixes and only one is in your hands:

| Cause | Meaning | Fix |
|---|---|---|
| `rate` | The unit counts agree and the money does not. | The per-unit rate is wrong. |
| `units` | The two sides metered different amounts of work. | A metering gap. No rate change closes it. |
| `coverage` | Only one side has the model at all. | Either VG never metered it, or the invoice does not itemise it. |

For a `rate` disagreement the report names **which authority produced VG's figure** and what the invoice implies the rate should be:

```
What to check:
  nova-3: units agree, cost does not. VG priced this from rate-card rule
  'cost|*|*|stt|deepgram|nova-3'. Your rate implies $0.00000583/unit; the
  invoice implies $0.00005833/unit (10.00x).
```

The rule is named by `rule_id` rather than by its price, because two rules at different scopes can carry the same rate and restating the number identifies nothing. When the catalogue produced the figure instead, the report says so: that is not a rule to edit, it means the published rate is stale or your contract differs from list, and the remedy is to [declare a cost rule](/configuration/voicegw-yaml) rather than to change one.

The unit label adapts to the provider:

- `tokens` for OpenAI (input + output, summed).
- `audio_s` for Deepgram (seconds; VG-side minutes are converted at the boundary).
- `chars` for Cartesia.

### CSV

Sixteen columns: `model, vg_units, provider_units, units_diff_abs, units_diff_pct, vg_cost_usd, provider_cost_usd, cost_diff_abs, cost_diff_pct, matched_in_vg, matched_in_provider, flagged, cause, pricing_sources, vg_rate, provider_rate`. The `flagged` column is `True`/`False` so spreadsheets can filter on it without re-deriving the threshold comparison, and `cause` carries the same diagnosis the text report explains, so a machine reader gets it too. `pricing_sources` is pipe-separated when a model was priced by more than one authority over the window.

### JSON

A nested document matching design §2.2:

```json
{
  "provider": "openai",
  "period": {"start": "2026-05-01", "end": "2026-05-31"},
  "rows": [
    {"model": "gpt-4o-mini", "vg_cost": 0.94, "provider_cost": 1.0, "cost_diff_abs": 0.06, "cost_diff_pct": 6.0, "flagged": true}
  ],
  "total": {"vg_cost": 7.10, "provider_cost": 7.31, "diff_abs": 0.21, "diff_pct": 2.91},
  "flagged_count": 1
}
```

<Note>
`total` sums only rows where both sides matched (missing-side rows excluded, mirroring the text format). `flagged_count` counts rows where `flagged=True`. Useful for piping into a monitoring or alerting tool.
</Note>

## Examples

### Diff May 2026 OpenAI usage

```bash
voicegw reconcile \
  --provider openai \
  --start 2026-05-01 --end 2026-05-31 \
  --provider-usage-file openai-may-2026.csv
```

### JSON output for piping

```bash
voicegw reconcile \
  --provider deepgram \
  --start 2026-05-01 --end 2026-05-31 \
  --provider-usage-file deepgram-may-2026.csv \
  --format json | jq '.rows[] | select(.cost_diff_pct > 5)'
```

<Tip>
The JSON output is a nested document keyed on `rows`, not a top-level array. Use the `.rows[]` selector as shown above.
</Tip>

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

## Related

[`voicegw export-costs`](/cli/export-costs) | [`voicegw costs`](/cli/costs)

## See also

- [Cost Reconciliation](/guide/cost-reconciliation): when to reconcile, how to interpret the diff, per-modality drift tolerance.
- [Reconcile File Formats](/reference/reconcile-formats): per-provider schemas this command expects.
