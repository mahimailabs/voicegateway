---
title: Cost Reconciliation
description: How to compare VoiceGateway's recorded costs against your provider invoices using voicegw reconcile.
---

# Cost Reconciliation

VoiceGateway records what it thinks you spent. Your provider records
what they actually charged. These numbers should agree. They will not
agree exactly, and that is by design: VoiceGateway estimates costs
from per-request unit counts at a snapshot rate, while your provider
bills against their authoritative meter and applies any discounts,
plan tiers, or post-hoc credits.

This page walks through reconciling the two numbers. The expected
drift is up to about 5% on LLM costs (per `pydantic/genai-prices`)
and lower on STT and TTS, where unit-of-billing maps directly to what
VoiceGateway records.

## When to reconcile

Run reconciliation:

- **Once during the first 30 days after deployment.** Catches setup
  errors (wrong rate sheet, miscounted units) before they accumulate.
- **After a provider rate change** (e.g., OpenAI ships a new model).
  Confirms the catalog refreshed in time.
- **Before sending invoices that aggregate AI costs to clients or
  internal teams.** If you bill milestone-based, this is the moment
  to verify VoiceGateway's number is defensible.
- **When VG's number diverges from your dashboard by >5%.** That is
  the gate that warrants investigation.

You do not need to reconcile every billing period. The reconciliation
flow is for spot-checks and incident response, not a monthly ritual.

## Prerequisites

- VoiceGateway recording requests against a SQLite store
  (`storage.path` set in `voicegw.yaml`).
- A provider usage export covering the same time window. See
  [Reconcile File Formats](/reference/reconcile-formats) for the
  per-provider schema VoiceGateway expects.
- The CLI installed and on your PATH:

```bash
voicegw --version
```

## Workflow

### 1. Pull the VoiceGateway side (optional inspection step)

If you want to see what VG recorded before running the diff:

```bash
voicegw export-costs \
  --start 2026-05-01 --end 2026-05-31 \
  --format csv > vg-may-2026.csv
```

`export-costs` writes one CSV row per request with timestamp,
project, modality, provider, model, units, cost, pricing source,
and status. Open in any spreadsheet to spot-check.

This step is not required for `reconcile`; the diff command reads
the same database directly.

### 2. Pull and convert the provider's export

Each provider's dashboard exposes a usage export. The exports are
not in VG's canonical format, so you convert once with a short
Python snippet (the conversions are documented per-provider on the
[reconcile-formats](/reference/reconcile-formats) page).

For OpenAI:

```bash
# 1. Download the CSV from platform.openai.com/usage for the period.
# 2. Run the conversion snippet from reconcile-formats.md.
python convert-openai.py \
  openai-may-2026.csv \
  openai-vg-format-may-2026.csv
```

For Deepgram, similar pattern with `console.deepgram.com/usage`.
For Cartesia, `play.cartesia.ai`.

### 3. Run reconcile

```bash
voicegw reconcile \
  --provider openai \
  --start 2026-05-01 --end 2026-05-31 \
  --provider-usage-file openai-vg-format-may-2026.csv
```

Default output is a text table:

```text
Model                                   VG tokens  Provider tokens     Δ%   VG cost  Prov cost        Δ$      Δ%
-------------------------------------------------------------------------------------------------------------
gpt-4o-mini                              1500000.0       1500000.0  +0.00% $0.0225  $0.0225   $+0.0000  +0.00%
gpt-4o                                    250000.0        260000.0  +3.85% $1.2500  $1.3000   $+0.0500  +3.85%
gpt-4o-staging                            100000.0             0.0  +0.00% $0.0050  $0.0000   $-0.0050   +0.00% (no provider data)
```

Three columns deserve a closer read:

- **Δ% on units.** How far off VG's unit count is from the provider's.
  Should be near zero. A non-zero unit-side diff means VG missed
  events (network drop during a streaming request, plugin event
  format changed) or counted differently than the provider.
- **Δ$ on cost.** Absolute dollar gap between VG's calculation and
  the provider's invoice for that model. Read with Δ% on cost.
- **Δ% on cost.** This is the headline number. The 5% guidance below
  applies to this column.

### Other output formats

`--format csv` emits the diff schema for spreadsheet ingestion.
`--format json` emits the same data as a JSON array, which is what
you want when piping into a monitoring or alerting tool.

## Interpreting the diff

### When the units agree but the cost diverges

The provider's per-model rate has drifted relative to what
VoiceGateway calculated.

For LLM costs, this means `pydantic/genai-prices` has not yet caught
up to the rate change, or the operator's account has a discount
(volume tier, BAA tier) the public catalog does not know about.
Update `genai-prices` (`uv pip install --upgrade genai-prices`) and
re-run; if the gap persists, your account is on a non-public rate
and the gap is the discount you are getting.

For STT and TTS costs, this means the per-minute or per-character
rate in `voicegateway/pricing/stt.py` (or `tts.py`) is stale. Refresh
that catalog entry against the provider's current price page and
ship a patch release. The 60-day staleness gate (Phase 2.6) will
also catch this if the pricing-source date drifts.

### When the units disagree

VoiceGateway is counting differently than the provider, regardless
of cost. Two common causes:

1. **Missed events.** A streaming request dropped before VG saw the
   `usage_collected` event from the LiveKit plugin. Check for
   warnings in `voicegw logs` matching `failed to record cost` or
   `incomplete usage`.
2. **Unit-of-billing mismatch.** The provider bills realtime audio
   differently from pre-recorded audio (Deepgram), or audio tokens
   differently from text tokens (OpenAI), and VG's catalog or model
   IDs do not split them. Look at the `model_id` column in
   `vg-may-2026.csv`; if the provider invoice has separate lines
   for `gpt-4o-audio` and `gpt-4o-mini` and VG only shows
   `gpt-4o-mini`, your `voicegw.yaml` is routing audio requests to
   the wrong model id and recording them under the wrong rate.

### When a model is on only one side

`(no provider data)` means VG logged requests for a model but the
provider's invoice has no line for it in the period. Two causes:
either VG is generating phantom requests (unlikely; VG only logs
requests that returned successfully, no retries logged), or the
provider's billing dashboard does not yet include very recent usage
(some providers lag 24-72 hours). Wait a day and re-pull.

`(no vg data)` means the provider charged for a model VG did not
record. This is the more interesting case: usually it means a
non-VG client is sharing the same API key. Check whether someone
else is hitting the API with the same credentials.

### How much drift is normal

| Modality | Expected | Investigate at |
| --- | --- | --- |
| LLM | within ~5% | >5% on cost, any % on units |
| STT | within ~1% | >2% on cost, any % on units |
| TTS | within ~2% | >3% on cost, any % on units |

LLM has wider tolerance because its rate sheet is a moving target;
`pydantic/genai-prices` updates within 24-48 hours of a published
change, but a same-day reconcile after a price change can show
several percent of drift that resolves itself the next day.

STT and TTS rates change rarely. A persistent gap there is more
likely a missed-event or wrong-model-id issue than a stale rate.

## Why VoiceGateway estimates instead of mirroring

We considered shipping a "real-time invoice mirroring" feature where
VoiceGateway pulls each provider's billing API and stores the
authoritative number. We did not, for three reasons:

1. **Provider billing APIs lag the request.** Several providers do
   not surface per-request cost until 24-72 hours after the request.
   Real-time cost dashboards (which is most of why operators use VG)
   need an immediate number, not a delayed one.
2. **Maintenance cost.** Each provider's billing API has different
   auth, format, and rate-limit shape. Maintaining seven of them
   inside VG is an ongoing tax.
3. **Reconciliation is the audit anyway.** The right model is "VG
   gives you a fast, defensible estimate; you reconcile when it
   matters." The reconcile command is the audit mechanism, and
   `pricing_source` attribution on every record (Phase 2.4) tells
   you exactly what catalog priced what.

If your billing requirements are FinOps-grade (every dollar must
match the invoice for accounting purposes), VoiceGateway is the
wrong tool for the cost-of-record. Use it for real-time observability
and reconcile against the provider invoice for the official number.

## See also

- [Reconcile File Formats](/reference/reconcile-formats): per-provider
  schema VoiceGateway expects.
- [`voicegw export-costs`](/cli/export-costs): inspect VG's per-request rows directly.
- [`voicegw reconcile`](/cli/reconcile): the diff command itself.
