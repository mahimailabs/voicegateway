---
title: Tenant attribution (advanced)
description: Single-tenant is the default. Pass a tenant_id only when you need per-call attribution, such as an agency splitting cost per end-user through the hosted cloud.
---
A VoiceGateway deployment is **single-tenant by default**: every record belongs to one operator, no tenant wiring required. Reach for `tenant_id` only when you need per-call attribution *within* one deployment: an agency splitting cost per end-user, or a SaaS product fanning usage out to the hosted cloud for per-tenant billing.

<Note>
`tenant_id` and `project` are separate and composable: `project` groups calls by agent/team/customer at the config level (see [Projects](/configuration/projects)); `tenant_id` stamps an individual end-user on top of that, on the wire. Pass both to `attach()` when you need both levels. Per-tenant billing and margin rollups run on the [hosted cloud](/hosted/quickstart).
</Note>

## Prerequisites

- VoiceGateway installed (`voicegw --version`).
- A running gateway daemon (`voicegw serve` or `voicegw onboard`).
- `voicegw.yaml` with `storage.path` pointing at your SQLite database.

## How it works

`attach()` accepts a `tenant_id` keyword argument. Passing it sets a context variable for that call; every STT, LLM, and TTS record written during the session reads it back and stores it. The id is capped at 128 UTF-8 characters. `attach()` raises `ValueError` above that; it does not truncate.

Sessions where `tenant_id` is never set store `NULL` and appear as "unattributed" downstream.

## Wire it into attach()

<Tabs>
  <Tab title="LiveKit">
    ```python
    from livekit.agents import Agent, AgentSession, JobContext
    from livekit.plugins import deepgram, openai, cartesia

    from voicegateway import attach


    async def entrypoint(ctx: JobContext):
        await ctx.connect()

        tenant_id = ctx.room.metadata  # e.g. "acme", from room metadata or a JWT claim

        session = AgentSession(
            stt=deepgram.STT(model="nova-3"),
            llm=openai.LLM(model="gpt-4o-mini"),
            tts=cartesia.TTS(model="sonic-3"),
        )
        attach(session, project="support", tenant_id=tenant_id)

        await session.start(agent=Agent(instructions="Be helpful."), room=ctx.room)
    ```
  </Tab>
  <Tab title="Pipecat">
    ```python
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.task import PipelineParams, PipelineTask
    from pipecat.services.deepgram.stt import DeepgramSTTService
    from pipecat.services.openai.llm import OpenAILLMService
    from pipecat.services.cartesia.tts import CartesiaTTSService

    from voicegateway import attach


    async def run_agent(tenant_id: str):
        stt = DeepgramSTTService(api_key=DEEPGRAM_API_KEY)
        llm = OpenAILLMService(api_key=OPENAI_API_KEY, model="gpt-4o-mini")
        tts = CartesiaTTSService(api_key=CARTESIA_API_KEY, voice_id=VOICE_ID)

        pipeline = Pipeline([transport.input(), stt, llm, tts, transport.output()])
        task = PipelineTask(
            pipeline, params=PipelineParams(enable_metrics=True, enable_usage_metrics=True)
        )
        attach(task, project="support", tenant_id=tenant_id)

        await PipelineRunner().run(task)
    ```
  </Tab>
</Tabs>

## Sub-tenants

Some deployments nest a sub-tenant below the top-level tenant (an agency serving clients that each have end users). Stamp it into `metadata.tenant_id` on the record; the top-level `tenant_id` is unchanged.

<Tip>
Sending to the VoiceGateway Cloud collector? Pass `metadata.tenant_id` in the ingest payload: the collector routes it alongside the top-level `tenant_id` so both appear in the hosted dashboard. See [Hosted quickstart](/hosted/quickstart).
</Tip>

## Reading per-tenant costs

Per-tenant rollups (revenue, cost, margin) run on the **hosted cloud**, which resolves the tenant from the verified `vk_` ingest key. In an OSS deployment `tenant_id` is stored but not exposed in a dashboard filter: read it back with SQL. The `sessions`, `requests`, and `turns` tables all carry `tenant_id`:

```sql
SELECT tenant_id, COUNT(*) AS session_count, SUM(total_cost_usd) AS total_cost
FROM sessions
WHERE started_at >= '2026-05-01'
GROUP BY tenant_id
ORDER BY total_cost DESC;
```

For a managed per-tenant billing view (rated revenue, recorded cost, margin), send your fleet to the hosted cloud and read `GET /v1/billing/usage`. See [Rating](/architecture/rating) and the [HTTP API reference](/api/http-api).

## Known limitations

<Note>
Deliberate scope decisions, not bugs.
</Note>

- **No dashboard tenant selector.** The OSS dashboard renders one deployment's totals; per-tenant slicing lives on the hosted cloud.
- **No re-tag.** Once a session has a non-NULL `tenant_id`, it can't be changed after the fact.
- **Virtual keys carry no RBAC scopes.** A verified virtual key grants the same access as a wildcard static key.

## See also

<CardGroup>
  <Card title="attach()" href="/guide/attach">
    Full signature and wiring reference for LiveKit and Pipecat.
  </Card>
  <Card title="Projects" href="/configuration/projects">
    Group cost by agent, team, or client at the config level.
  </Card>
  <Card title="Multi-project example" href="/configuration/projects">
    Code example using multiple projects and tenants side by side.
  </Card>
</CardGroup>
