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

Save the snippet below as `convert-openai.py` and invoke it with the
source export and the desired destination filename:
`python convert-openai.py <openai-export.csv> <vg-format.csv>`.

```python
import csv
import sys
from collections import defaultdict
from pathlib import Path

src = Path(sys.argv[1])
dst = Path(sys.argv[2])

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

## Deepgram

### Canonical input shape

`voicegw reconcile --provider deepgram --provider-usage-file <FILE>`
expects either CSV or JSON. The format is auto-detected from the file
extension; the schemas are equivalent.

**CSV** (header row required, column order does not matter):

```csv
model,audio_seconds,n_requests,cost_usd
nova-3,180000.0,1500,8.700
nova-2,42000.5,300,2.100
```

**JSON** (top-level array of objects):

```json
[
  {
    "model": "nova-3",
    "audio_seconds": 180000.0,
    "n_requests": 1500,
    "cost_usd": 8.700
  },
  {
    "model": "nova-2",
    "audio_seconds": 42000.5,
    "n_requests": 300,
    "cost_usd": 2.100
  }
]
```

### Field semantics

| Field | Required | Notes |
| --- | --- | --- |
| `model` | yes | Deepgram model id without the `deepgram/` prefix. VoiceGateway prepends the prefix when matching against its own logs. |
| `audio_seconds` | yes | Aggregate transcribed audio duration, in seconds, across the reconcile window. Deepgram bills per-minute, so audio-minutes from the dashboard multiplied by 60 is the value to use. Float allowed. |
| `n_requests` | optional | Used for sanity checks; reconcile reports a warning if VG's request count diverges by more than 10%. Omit if your export does not include it. |
| `cost_usd` | yes | Aggregate cost Deepgram charged for that model in the window. The reconcile diff is computed against this number. |

Real-time vs pre-recorded vs streaming distinctions are not in this
schema. Sum the durations across all delivery modes for a given model
into a single row; if your account uses different rate cards per
mode, split into separate model rows (e.g., `nova-3-realtime`,
`nova-3-prerecorded`) and record those same suffixed names in
`voicegw.yaml` so VG's logs match.

### Producing the canonical format from the Deepgram console

Deepgram's [console](https://console.deepgram.com/usage) exposes a
usage page with per-model rollups. Two paths to the canonical CSV:

**Path A: console export.** Click "Export CSV" on the Usage page for
your billing window. The exported columns this guide assumes:

- `model` (or `model_name`): the model id.
- `seconds_total` (or `duration_seconds_total`): maps to
  `audio_seconds`. If your export reports minutes, multiply by 60.
- `requests_total`: maps to `n_requests`.
- `total_cost_usd` (or `amount_usd`): maps to `cost_usd`.

**Path B: management API.** The
[GET /v1/projects/{id}/usage/requests](https://developers.deepgram.com/reference/management-api/usage/list-usage-requests)
endpoint returns per-request rows; aggregate them per-model client-side.

A short Python conversion for Path A:

Save the snippet below as `convert-deepgram.py` and invoke it as
`python convert-deepgram.py <deepgram-export.csv> <vg-format.csv>`.

```python
import csv
import sys
from collections import defaultdict
from pathlib import Path

src = Path(sys.argv[1])
dst = Path(sys.argv[2])

agg = defaultdict(lambda: {"seconds": 0.0, "requests": 0, "cost": 0.0})
with src.open() as f:
    for row in csv.DictReader(f):
        m = row["model"]
        agg[m]["seconds"] += float(row.get("seconds_total", 0))
        agg[m]["requests"] += int(row.get("requests_total", 0))
        agg[m]["cost"] += float(row.get("total_cost_usd", 0))

with dst.open("w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["model", "audio_seconds", "n_requests", "cost_usd"])
    for model, v in agg.items():
        w.writerow([model, v["seconds"], v["requests"], f"{v['cost']:.6f}"])
```

If your Deepgram export reports minutes instead of seconds, replace
`float(row.get("seconds_total", 0))` with
`float(row.get("minutes_total", 0)) * 60`.

### Why audio_seconds and not minutes

Deepgram's billing dashboards display minutes by default, but VG
records audio duration in seconds (the unit `livekit-plugins-deepgram`
emits on its `usage_collected` event, and the unit
`voicegateway/pricing/stt.py` calculates against). Storing seconds in
the canonical reconcile file keeps both sides of the comparison in
the same unit. If your export hands you minutes, the conversion above
multiplies in.

## Cartesia

### Canonical input shape

`voicegw reconcile --provider cartesia --provider-usage-file <FILE>`
expects either CSV or JSON. The format is auto-detected from the file
extension; the schemas are equivalent.

**CSV** (header row required, column order does not matter):

```csv
model,characters,credits,n_requests,cost_usd
sonic-3,2500000,250000,1000,30.000
sonic-2,500000,50000,200,6.000
```

**JSON** (top-level array of objects):

```json
[
  {
    "model": "sonic-3",
    "characters": 2500000,
    "credits": 250000,
    "n_requests": 1000,
    "cost_usd": 30.000
  },
  {
    "model": "sonic-2",
    "characters": 500000,
    "credits": 50000,
    "n_requests": 200,
    "cost_usd": 6.000
  }
]
```

### Field semantics

| Field | Required | Notes |
| --- | --- | --- |
| `model` | yes | Cartesia model id without the `cartesia/` prefix. VoiceGateway prepends the prefix when matching against its own logs. |
| `characters` | yes | Aggregate synthesized character count across the reconcile window. This is what VG records (the unit `livekit-plugins-cartesia` emits on its `usage_collected` event), so the reconcile diff against VG's logs uses this column. Set to 0 if your export only ships credits. |
| `credits` | optional | Aggregate Cartesia credits consumed in the window. Cartesia's billing portal exposes credits as the primary unit; surfacing them here lets reconcile cross-check the credits-to-USD math even when characters are absent. |
| `n_requests` | optional | Used for sanity checks; reconcile reports a warning if VG's request count diverges by more than 10%. Omit if your export does not include it. |
| `cost_usd` | yes | Aggregate cost Cartesia charged for that model in the window. The reconcile diff is computed against this number. Convert credits-to-USD via your account's rate sheet (see below). |

Voice-id selection (Cartesia lets you switch voices per-request) is not
in this schema. Voice id does not affect billing in Cartesia's current
pricing; aggregate across all voices for a given model into a single
row. If a future Cartesia rate card differentiates by voice, split into
suffixed model rows (e.g., `sonic-3-staging`, `sonic-3-production`) and
mirror those names in `voicegw.yaml`.

### Producing the canonical format from the Cartesia portal

Cartesia's [billing portal](https://play.cartesia.ai/) lists usage by
model with both a character count and a credits column. The portal CSV
columns this guide assumes:

- `model` (or `model_id`): the model id.
- `chars_synthesized` (or `characters_total`): maps to `characters`.
- `credits_used` (or `credits_consumed`): maps to `credits`.
- `requests` (or `n_requests`): maps to `n_requests`.
- `cost_usd` (or `total_cost`): maps to `cost_usd`.

A short Python conversion. Save as `convert-cartesia.py` and invoke
as `python convert-cartesia.py <cartesia-export.csv> <vg-format.csv>`.

```python
import csv
import sys
from collections import defaultdict
from pathlib import Path

src = Path(sys.argv[1])
dst = Path(sys.argv[2])

agg = defaultdict(lambda: {"chars": 0, "credits": 0, "requests": 0, "cost": 0.0})
with src.open() as f:
    for row in csv.DictReader(f):
        m = row["model"]
        agg[m]["chars"] += int(row.get("chars_synthesized", 0))
        agg[m]["credits"] += int(row.get("credits_used", 0))
        agg[m]["requests"] += int(row.get("requests", 0))
        agg[m]["cost"] += float(row.get("cost_usd", 0))

with dst.open("w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["model", "characters", "credits", "n_requests", "cost_usd"])
    for model, v in agg.items():
        w.writerow([model, v["chars"], v["credits"], v["requests"], f"{v['cost']:.6f}"])
```

If your Cartesia portal export does not ship `cost_usd` directly,
multiply `credits_used` by your account's USD-per-credit rate (visible
on the billing portal's rate sheet) and write that into `cost_usd`.

### Why both characters and credits

Cartesia is currently credit-based: the billing portal's primary unit
is credits, and the credits-to-USD conversion depends on the account's
plan tier. VG records characters (the LiveKit plugin's
`usage_collected` event ships character counts, not credits) and
calculates an estimated cost via a documented per-character rate in
`voicegateway/pricing/tts.py`. Surfacing both columns lets reconcile
report two diffs:

- VG's character-count vs Cartesia's character-count (a units check).
- VG's calculated USD vs Cartesia's billed USD (the cost diff).

If the units agree but the dollars disagree, VG's per-character rate
in `pricing/tts.py` is stale relative to your plan; refresh that
catalog entry and re-run.

If your account is invoiced as flat-USD (not credits), set
`credits = 0` and only the cost diff is meaningful.

## Other providers

Reconcile schemas for Anthropic, ElevenLabs, AssemblyAI, and
additional providers can be added as sub-items of a future
v0.1.x release. Open an issue at
[github.com/mahimailabs/voicegateway](https://github.com/mahimailabs/voicegateway)
if you need a provider that is not listed here.
