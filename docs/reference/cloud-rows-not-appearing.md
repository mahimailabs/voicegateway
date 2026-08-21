---
title: "Cloud: rows not appearing"
description: Why telemetry rows do not show up on a hosted collector, and why a $0.00 row is usually not the problem you think it is.
---

Telemetry is missing from the dashboard, or every row reads `$0.00`. These are different problems with different causes.

## Nothing is arriving at all

Export all three variables in the same process that runs your agent:

```bash
export VOICEGW_COLLECTOR_URL="https://<your-collector-host>"
export VOICEGW_API_KEY="vk_your_ingest_key"
export VOICEGW_PROJECT="my-agent"
```

`attach()` reads `VOICEGW_COLLECTOR_URL` and `VOICEGW_API_KEY` at call time. If either name is misspelled, nothing is sent and **nothing raises**: the sink is best-effort by design, so it buffers, fails, and retries quietly rather than taking your agent down over a telemetry problem.

That silence is deliberate, and it means a missing row is never reported as an error. Check the variable names first.

See the [hosted quickstart](/hosted/quickstart) for the full setup.

## Rows arrive but every cost is `$0.00`

A `$0.00` row is a real row. **Four different things produce one**, and they are not distinguished by the cost column, which is why the cost column is the wrong place to look:

| What happened | `cost_usd` | `pricing_source` |
|---|---|---|
| Self-hosted model (`local/*`, `ollama/*`) | `0.0` | `voicegateway-local` |
| Model not in the pricing catalogue | `0.0` | `""` (empty) |
| Model matched, but the catalogue has no rate for it | `0.0` | `voice-prices-unrated` |
| Genuinely free or trivial usage | `0.0` | `voice-prices@<version>` |

**`pricing_source` is what tells them apart.** An empty `pricing_source` means VoiceGateway priced nothing because it did not recognise the model, and it logs a warning when that happens: `No pricing data for <modality> model '<model>'; cost recorded as $0`.

`voice-prices-unrated` is the subtle one. Some catalogue entries match a model and carry no rate at all: `deepgram/nova-general` matches the entry `nova`, whose price fields are all empty. The lookup succeeds, no rate is applied, and the total is zero. That zero is not a price, so the row is tagged `voice-prices-unrated` rather than `voice-prices@<version>` and logs `matched the catalog but it carries no rate for <units>`. The remedy differs from an unknown model: the entry exists already and needs a rate, rather than needing to be added.

The tag makes no claim about *why* there is no rate. A deliberately free tier and a missing rate are identical in the catalogue data, so VoiceGateway reports what it observed instead of guessing which one you have.

Self-hosted models are not a defect. They run on hardware you already pay for, and VoiceGateway records the usage without inventing a price for it.

An unrecognised model usually means one of two things. Either your collector is pinned to an older `voice-prices` than your agent, so a model your agent knows is one the collector has never heard of, or the model genuinely has no catalogue entry yet. Check the collector's `pricing_source` version before assuming the second.

## A row with no model at all

Rows with an empty `model_id` and modality `eou` are end-of-utterance timing records. No provider call happened, so no model and no cost is the correct answer rather than a gap. They carry their measurements in `metadata.eou`.

Use `billable_requests` rather than `requests` when dividing cost by a call count: `requests` counts every stored row, including these and failed calls, and a cost per call built on it reads low.

See [cost tracking](/architecture/cost-tracking) for how request costs are calculated.
