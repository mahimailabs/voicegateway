---
title: Tenant attribution (advanced)
description: Single-tenant is the default. Pass a tenant_id only when you need per-call attribution, such as an agency splitting cost per end-user through the hosted cloud.
---

# Tenant attribution (advanced)

A VoiceGateway deployment is **single-tenant by default**: every record belongs to one operator and no tenant wiring is required. You only reach for `tenant_id` when you need per-call attribution *within* one deployment, for example an agency splitting cost per end-user, or a SaaS product that fans usage out to the hosted cloud for per-tenant billing.

`attach()` accepts a `tenant_id` keyword argument. When you pass it, every STT, LLM, and TTS record from that session is stamped with the value on the wire. The stamp is what the **hosted cloud** and downstream analysis use to attribute cost per customer. The local OSS dashboard has no tenant selector; it renders your single deployment's totals.

<Note>
This page covers the agent-side wiring (the wire). For project-level grouping (one project per agency client), see [Agency quickstart](/guide/agency-quickstart). The two are composable: you can pass both `project=` and `tenant_id=` to `attach()`. For per-tenant billing and margin rollups, that runs on the [hosted cloud](/hosted/quickstart).
</Note>

## Prerequisites

- VoiceGateway installed (`voicegw --version` to confirm).
- A running gateway daemon (`voicegw serve` or `voicegw onboard`).
- `voicegw.yaml` with `storage.path` pointing to your SQLite database.

## How tenant attribution works

`attach()` accepts a `tenant_id` keyword argument. Every cost and latency record written during that session carries the value you pass. Sub-tenants or per-call overrides can be stamped via `metadata.tenant_id` on the record after it is created.

The tenant id is bounded at 128 UTF-8 characters. Unicode is allowed.

Sessions where `tenant_id` is not set store `NULL` (the single-tenant default) and appear as "unattributed" downstream.

## Step 1: wire attach() with a tenant id

Your agent code knows the caller's tenant at connection time (from room metadata, a custom JWT claim, or a header). Pass it straight into `attach()`.

<Tabs>
  <Tab title="LiveKit">
    ```python
    from livekit.agents import Agent, AgentSession, JobContext
    from livekit.plugins import deepgram, openai, cartesia

    from voicegateway import attach


    async def entrypoint(ctx: JobContext):
        await ctx.connect()

        # Resolve the tenant from room metadata or a JWT claim.
        tenant_id = ctx.room.metadata  # e.g. "acme"

        session = AgentSession(
            stt=deepgram.STT(model="nova-3"),
            llm=openai.LLM(model="gpt-4o-mini"),
            tts=cartesia.TTS(model="sonic-3"),
        )

        # Every record from this session is stamped with tenant_id.
        attach(session, project="support", tenant_id=tenant_id)

        await session.start(
            agent=Agent(instructions="Be helpful."),
            room=ctx.room,
        )
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
            pipeline,
            params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        )

        # Every record from this pipeline is stamped with tenant_id.
        attach(task, project="support", tenant_id=tenant_id)

        runner = PipelineRunner()
        await runner.run(task)
    ```
  </Tab>
</Tabs>

## Step 2: carry a sub-tenant via metadata

Some deployments nest sub-tenants below the top-level tenant (for example, a platform serving agencies that each have end clients). The record's `metadata` field lets you attach an additional `tenant_id` for downstream analysis without changing the top-level attribution.

```python
# After attach(), stamp sub-tenant data in metadata before the session starts.
session_id = attach(session, project="platform", tenant_id="agency-acme")
# The sub-tenant is carried on the wire via record metadata.
# See the Fleet Collector ingest flow for how metadata.tenant_id is routed.
```

<Tip>
If you are sending data to the VoiceGateway Cloud collector, pass `metadata.tenant_id` in the ingest payload. The collector routes it alongside the top-level `tenant_id` so both levels appear in the hosted dashboard. See [Hosted quickstart](/hosted/quickstart) for the collector env vars.
</Tip>

## Step 3: read per-tenant costs

Per-tenant rollups (revenue, cost, margin) run on the **hosted cloud**, which resolves the tenant from the verified `vk_` ingest key and bills per tenant. In an OSS deployment, the `tenant_id` is stamped on the wire and stored, but you read it back with SQL rather than a dashboard filter.

### SQL

The `sessions` table carries `tenant_id`. For ad-hoc analysis:

```sql
SELECT
    tenant_id,
    COUNT(*)             AS session_count,
    SUM(total_cost_usd)  AS total_cost
FROM sessions
WHERE started_at >= '2026-05-01'
GROUP BY tenant_id
ORDER BY total_cost DESC;
```

The `requests` and `turns` tables also carry `tenant_id`, so any join-and-aggregate query can group by tenant without schema changes.

For a managed per-tenant billing view (rated revenue, recorded cost, and margin per tenant), send your fleet to the hosted cloud and read `GET /v1/billing/usage`. See [Rating](/architecture/rating) and the [HTTP API reference](/api/http-api).

## Known limitations

<Note>
These are deliberate scope decisions, not bugs.
</Note>

- **The OSS dashboard has no tenant selector.** It renders your single deployment's totals; per-tenant slicing lives on the hosted cloud. Read the stored `tenant_id` with SQL for local analysis.
- **No re-tag affordance.** Once a session has a non-NULL `tenant_id`, it cannot be changed after the fact.
- **Virtual keys do not carry RBAC scopes.** A verified virtual key grants the same access as a wildcard static key.

## See also

<CardGroup>
  <Card title="attach()" href="/guide/attach">
    Full signature and wiring reference for LiveKit and Pipecat.
  </Card>
  <Card title="Agency quickstart" href="/guide/agency-quickstart">
    One agency running many client projects, with per-client budgets.
  </Card>
  <Card title="Multi-project example" href="/examples/multi-project">
    Code example using multiple projects and tenants side by side.
  </Card>
  <Card title="Configuration: projects" href="/configuration/projects">
    Set daily budgets and routing rosters per project in voicegw.yaml.
  </Card>
</CardGroup>
</content>
</invoke>
