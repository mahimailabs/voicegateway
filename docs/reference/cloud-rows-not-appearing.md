---
title: "Cloud: rows not appearing"
description: Why telemetry rows do not show up (or show $0.00) on the hosted dashboard, and how to fix it.
---

# Cloud: rows not appearing

## Confirm the environment variables

Export all three variables in the same process that runs your agent:

```bash
export VOICEGW_COLLECTOR_URL="https://<your-cloud-api-host>"
export VOICEGW_API_KEY="vk_your_ingest_key"
export VOICEGW_PROJECT="my-agent"
```

Check the variable names carefully. `attach()` reads
`VOICEGW_COLLECTOR_URL` and `VOICEGW_API_KEY`. If either name is wrong,
telemetry will not be sent to the hosted collector.

See the [hosted quickstart](/hosted/quickstart) for the full setup.

## A `$0.00` row is still a real row

A row showing `$0.00` is not missing telemetry. Free and local models,
including `local/*` and `ollama/*`, are priced at zero.

Models with unknown pricing resolve to `None`, so their cost may appear blank
instead of `$0.00`.

See [cost tracking](/architecture/cost-tracking) for details about how request
costs are calculated.