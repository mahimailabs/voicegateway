---
title: Cloud quickstart
description: Send your LiveKit or Pipecat agent's telemetry to VoiceGateway Hosted Cloud in three environment variables. You keep your own provider keys; we store telemetry rows only.
---

# Cloud quickstart

VoiceGateway Hosted Cloud is a managed collector, ClickHouse-backed storage, and shared dashboard at [dash.voicegateway.dev](https://dash.voicegateway.dev). You bring your own provider API keys (OpenAI, Deepgram, Cartesia, and the rest). Your agent pushes per-call telemetry (spend, latency, call counts) to the hosted ingest endpoint, and the dashboard renders it. We store the telemetry rows, nothing else.

The same `attach()` and `guard()` calls you use for self-hosting work identically here. Only the sink changes: a remote collector instead of a local SQLite file.

<Note>
The hosted cloud never receives your call audio or transcripts. `attach()` reads per-component metrics events (cost, latency, errors) and pushes only those numeric rows to the collector.
</Note>

---

<Steps>

<Step title="Sign up and get an ingest key">

Open [dash.voicegateway.dev](https://dash.voicegateway.dev) and create an account. After signing in, go to **Settings > Ingest keys** and issue a new key.

The key looks like `vk_...`. Copy it into your secret store immediately. The dashboard shows the full key only once.

The same page shows your personal ingest URL, in the form:

```
https://<your-cloud-api-host>/v1/ingest
```

Copy that URL too. You will need both values in the next step.

</Step>

<Step title="Set the three environment variables">

`attach()` reads three variables. Set them in your agent's runtime environment (your shell, Dockerfile, Railway/Fly secret store, or `.env` file):

```bash
export VOICEGW_COLLECTOR_URL="https://<your-cloud-api-host>"
export VOICEGW_API_KEY="vk_your_ingest_key"
export VOICEGW_PROJECT="my-agent"
```

When `VOICEGW_COLLECTOR_URL` is present, `attach()` builds a remote sink that batches rows and pushes them to the hosted collector instead of writing to local SQLite. The `vk_` key authenticates the request and maps every row to your tenant. `VOICEGW_PROJECT` tags every captured row so costs appear per-project on the dashboard.

<Tip>
Use your platform's secret manager so the `vk_` key never lands in source control.
</Tip>

</Step>

<Step title="Add attach() to your agent">

Call `attach()` with no `project=` argument and it picks up `VOICEGW_PROJECT` automatically. Pass `project=` explicitly to override the env var for a specific call.

<Tabs>
  <Tab title="LiveKit">

```python
import voicegateway
from livekit.agents import AgentSession

async def entrypoint(ctx):
    session = AgentSession(...)
    voicegateway.attach(session)  # project comes from VOICEGW_PROJECT
    await session.start(...)
```

  </Tab>
  <Tab title="Pipecat">

```python
import voicegateway
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineParams, PipelineTask

pipeline = Pipeline([...])
task = PipelineTask(
    pipeline,
    params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
)
voicegateway.attach(task)  # project comes from VOICEGW_PROJECT
```

  </Tab>
</Tabs>

That is the whole integration. The three env vars point telemetry at the hosted collector and tag every row with your project. Pass `project=` to `attach()` to override the env var for a specific session.

</Step>

<Step title="Watch costs land on the dashboard">

Run a call. Within a few seconds, open [dash.voicegateway.dev](https://dash.voicegateway.dev) and navigate to your project. You will see spend, latency, and call counts broken down by STT, LLM, and TTS.

<Note>
If rows do not appear after the first call, check that all three env vars are exported in the process that runs your agent, then see [Troubleshooting](/reference/troubleshooting).
</Note>

</Step>

</Steps>

---

## Pricing

| Plan | Price |
|------|-------|
| Free | Permanent free tier |
| Pro | $9 for the first month, then $29/mo |
| Agency | $199/mo |

See [voicegateway.dev](https://voicegateway.dev) for the full breakdown and current usage-metered details.

---

## Next steps

<CardGroup cols={2}>
  <Card title="attach() reference" href="/guide/attach">
    Full options for the attach call: project, budget, metadata, and flush behaviour.
  </Card>
  <Card title="guard() reference" href="/guide/guard">
    Add real-time budget enforcement and per-call spend limits on top of attach.
  </Card>
  <Card title="Self-host quickstart" href="/guide/quick-start">
    Run the collector locally or on your own infra instead of using hosted cloud.
  </Card>
  <Card title="Which path fits?" href="/guide/decision-tree">
    Not sure whether cloud or self-host is right? Use the decision tree.
  </Card>
</CardGroup>
