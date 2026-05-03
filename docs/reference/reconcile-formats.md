---
title: Reconcile File Formats
description: Schemas the voicegw reconcile command expects when reading provider usage exports.
---

# Reconcile File Formats

`voicegw reconcile` (Phase 4.3 of v0.1.0) compares VoiceGateway's
recorded costs against a provider's usage export. Different providers
ship different exports, so VoiceGateway defines one canonical
reconcile-input format per provider, and documents how to produce that
format from each provider's native export.

This page is the schema reference. The walkthrough that ties it to
the day-to-day reconciliation workflow lives at
[Cost Reconciliation](/guide/cost-reconciliation) (added in Phase 4.4).

## OpenAI

### Canonical input shape

`voicegw reconcile --provider openai --provider-usage-file <FILE>`
expects either CSV or JSON. The format is auto-detected from the file
extension; the schemas are equivalent.

**CSV** (header row required, column order does not matter):

```csv
model,input_tokens,output_tokens,n_requests,cost_usd
gpt-4o-mini,1000000,500000,500,0.225
gpt-4o,250000,125000,200,2.500
```

**JSON** (top-level array of objects):

```json
[
  {
    "model": "gpt-4o-mini",
    "input_tokens": 1000000,
    "output_tokens": 500000,
    "n_requests": 500,
    "cost_usd": 0.225
  },
  {
    "model": "gpt-4o",
    "input_tokens": 250000,
    "output_tokens": 125000,
    "n_requests": 200,
    "cost_usd": 2.500
  }
]
```

### Field semantics

| Field | Required | Notes |
| --- | --- | --- |
| `model` | yes | OpenAI model id without the `openai/` prefix. VoiceGateway prepends the prefix when matching against its own logs. |
| `input_tokens` | yes | Aggregate prompt/context tokens across the reconcile window. Set to 0 if you only have output counts. |
| `output_tokens` | yes | Aggregate generated tokens. Set to 0 if not applicable. |
| `n_requests` | optional | Used for sanity checks; reconcile reports a warning if VG's request count diverges by more than 10%. Omit if your export does not include it. |
| `cost_usd` | yes | Aggregate cost OpenAI charged for that model in the window. The reconcile diff is computed against this number. |

Cached tokens, audio tokens, and embedding-model lines (if present in
your export) are not in this schema. Drop those rows before running
reconcile, or include them with their own model id (e.g.,
`gpt-4o-mini-audio-preview`) and let VG report them as unmatched.

### Producing the canonical format from the OpenAI dashboard

The OpenAI usage dashboard at
[platform.openai.com/usage](https://platform.openai.com/usage) ships a
"Download CSV" button. Its column set varies over time; the columns
this guide assumes are stable:

- `model` (or `snapshot_id`): the model id.
- `n_context_tokens_total`: maps to `input_tokens` in VoiceGateway's
  schema.
- `n_generated_tokens_total`: maps to `output_tokens`.
- `n_requests`: maps to `n_requests`.
- `cost_total_usd`: maps to `cost_usd`. If the dashboard CSV does not
  include this column directly, sum the `cost_input_usd` and
  `cost_output_usd` columns.

A short Python conversion (one-time, drop alongside your VG checkout):

```python
import csv
from collections import defaultdict
from pathlib import Path

src = Path("openai-usage-2026-05-01-to-2026-05-31.csv")
dst = Path("openai-vg-format.csv")

agg = defaultdict(lambda: {"input": 0, "output": 0, "requests": 0, "cost": 0.0})
with src.open() as f:
    for row in csv.DictReader(f):
        m = row["model"]
        agg[m]["input"] += int(row.get("n_context_tokens_total", 0))
        agg[m]["output"] += int(row.get("n_generated_tokens_total", 0))
        agg[m]["requests"] += int(row.get("n_requests", 0))
        agg[m]["cost"] += float(row.get("cost_total_usd", 0))

with dst.open("w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["model", "input_tokens", "output_tokens", "n_requests", "cost_usd"])
    for model, v in agg.items():
        w.writerow([model, v["input"], v["output"], v["requests"], f"{v['cost']:.6f}"])
```

If your OpenAI export schema differs from the column names above, the
parser will surface the column names that are present so you can
adjust the conversion. Open an issue at
[github.com/mahimailabs/voicegateway](https://github.com/mahimailabs/voicegateway)
if your export shape diverges enough that this conversion is painful;
we treat reconcile-format friction as a reconciliation bug.

### Why a normalized format and not a direct dashboard parser

OpenAI's dashboard CSV columns have changed during 2025-2026 as new
modalities (audio, embeddings, batch) shipped. A direct parser inside
VoiceGateway would tie us to whatever shape was current the week we
shipped. The normalized format is small enough that the conversion
above is a few lines of Python, and stable enough that VoiceGateway's
reconcile semantics do not regress when OpenAI changes their export.

When real users surface that the conversion is annoying, we will ship
a built-in `voicegw reconcile-import openai <NATIVE-FILE>` helper.
Until then: the small Python snippet is the contract.

## Other providers

Definitions for Deepgram, Cartesia, and additional providers ship in
follow-up Phase 4.3 sub-items. This page will append a section per
provider as those format definitions land.
