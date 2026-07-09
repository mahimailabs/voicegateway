---
title: Hosted quickstart
description: Send LiveKit agent telemetry to the hosted VoiceGateway cloud in two environment variables. You keep your own provider keys, we store telemetry rows only.
---

# Hosted quickstart

The hosted VoiceGateway cloud at [dash.voicegateway.dev](https://dash.voicegateway.dev) is a bring-your-own-keys observability service. You keep your own provider API keys (OpenAI, Deepgram, Cartesia, and the rest) exactly where they already live, in your own agent's environment. Your LiveKit agent pushes per-call telemetry (spend, latency, and call counts) to the hosted collector, and the dashboard renders it. We store the telemetry rows, nothing else.

This is the same `attach()` flow as the self-hosted collector, pointed at the hosted ingest endpoint instead of a collector you run yourself.

## What we do NOT store

The hosted cloud never receives your call audio or transcripts. `attach()` reads per-component metrics events (cost, latency, errors) and pushes only those numeric rows. No recording, no transcript text, no message content leaves your agent.

## Onboarding

Two environment variables and one line of code. As soon as a call runs, spend, latency, and calls appear on the dashboard.

### 1. Issue an ingest key

Open [dash.voicegateway.dev](https://dash.voicegateway.dev), go to the **Ingest keys** page, and issue a key. It looks like `vk_...`. Copy it into your secret store: the dashboard shows the full key once. The same page shows your ingest URL, of the form `https://<your-cloud-api-host>/v1/ingest`.

### 2. Set the two environment variables

These are the exact variables the engine's `attach()` reads:

- `VOICEGW_COLLECTOR_URL`: your hosted ingest URL from the Ingest keys page, of the form `https://<your-cloud-api-host>/v1/ingest`.
- `VOICEGW_API_KEY`: your `vk_` ingest key.

```bash
export VOICEGW_COLLECTOR_URL="https://<your-cloud-api-host>/v1/ingest"
export VOICEGW_API_KEY="vk_your_ingest_key"
```

When `VOICEGW_COLLECTOR_URL` is set, `attach()` builds a remote sink that batches rows and pushes them to the hosted collector instead of writing to local SQLite.

### 3. Attach in your worker

Tag each agent with its project via the `project` argument to `attach()`. There is no project environment variable: the project id is passed in code.

```python
import voicegateway
from livekit.agents import AgentSession

async def entrypoint(ctx):
    session = AgentSession(...)
    voicegateway.attach(session, project="my-agent")
    await session.start(...)
```

That's the whole integration. The two exported variables point telemetry at the hosted collector; the `attach(session, project="my-agent")` call binds the session and tags every captured row with the project. Run a call and watch spend, latency, and call counts land on the dashboard.

## Pricing

- **Free**: permanent free tier.
- **Pro**: $9 for the first month, then $29/mo. Cancel anytime.
- **Agency**: $199/mo.

See [voicegateway.dev](https://voicegateway.dev) for the full breakdown and current usage-metered details.
