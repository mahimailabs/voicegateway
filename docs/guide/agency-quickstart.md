---
title: Agency quickstart
description: Run one VoiceGateway deployment for many downstream clients, each with its own project, routing roster, budget, and white-label dashboard.
---

# Agency quickstart

An agency runs one VoiceGateway deployment and provisions a separate project for each downstream client. Each project gets its own routing roster, latency budget, spend cap, and white-label dashboard branding. Cost data stays separated by project, so you can pull a per-client cost report without manual filtering.

This guide walks the end-to-end provisioning flow for one new client.

<Note>
Projects separate cost and routing at the client level. Tenant attribution (covered in [Tenant attribution](/guide/multi-tenant-quickstart)) stamps cost within a project at the end-user level on the wire. The two are composable: pass `project=` and `tenant_id=` together in `attach()` when you need both levels. Per-tenant billing rollups run on the hosted cloud.
</Note>

## Prerequisites

- VoiceGateway installed (`voicegw --version`).
- Daemon running (`voicegw serve` or `voicegw onboard`).
- `voicegw.yaml` with at least one project configured. See [Configuration: projects](/configuration/projects) for the full schema.

## Step 1: add the client project to voicegw.yaml

Open `voicegw.yaml` and add a `projects` entry for the client. Set a `daily_budget` to cap spend automatically and a `routing` block to constrain which providers the router may choose.

```yaml
projects:
  acme:
    name: Acme Voice
    daily_budget: 25.0
    routing:
      budget_ms: 1200
      fallback_to_fastest: true
      rosters:
        stt: [deepgram, assemblyai]
        llm: [groq, openai]
        tts: [cartesia, elevenlabs]
```

Provider order within each roster is preference: the router picks the first provider that fits the latency budget. Restart the daemon after saving. In-flight sessions keep their original provider triple; the new roster applies from the next session start.

<Tip>
The default `budget_ms` is 1500 ms (a typical conversational target). Tighten to 800-1000 ms for high-energy customer-service scenarios. The Routing view in the dashboard shows observed p50 per provider so you can right-size after a few hundred sessions.
</Tip>

## Step 2: verify the router before traffic lands

The `route` CLI subgroup is read-only. Use it to confirm the config is correct before the first real call.

```bash
voicegw route show acme
# Project acme  budget_ms=1200
#
# Rosters
#   stt   deepgram, assemblyai
#   llm   groq, openai
#   tts   cartesia, elevenlabs
```

```bash
voicegw route simulate acme
# Project acme simulated route:
#   STT: deepgram     (250 ms baseline)
#   LLM: groq         (80 ms baseline)
#   TTS: cartesia     (150 ms baseline)
#   Predicted total: 480 ms
#   Under budget (1200 ms): yes
```

```bash
# Override one modality to compare.
voicegw route simulate acme --llm openai
# LLM: openai (300 ms baseline)
# Predicted total: 700 ms
```

After production traffic accrues, the rollup worker (runs every 15 minutes) fills `latency_observations` and the router prefers observed p50 over the curated baselines.

## Step 3: wire the agent to the project

In the agent code, pass `project="acme"` to `attach()`. Every record written during that session is tagged with the project id.

<Tabs>
  <Tab title="LiveKit">
    ```python
    from livekit.agents import Agent, AgentSession, JobContext
    from livekit.plugins import deepgram, openai, cartesia

    from voicegateway import attach


    async def entrypoint(ctx: JobContext):
        await ctx.connect()

        session = AgentSession(
            stt=deepgram.STT(model="nova-3"),
            llm=openai.LLM(model="gpt-4o-mini"),
            tts=cartesia.TTS(model="sonic-3"),
        )

        attach(session, project="acme")

        await session.start(
            agent=Agent(instructions="Be the Acme support agent."),
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


    async def run_acme_agent():
        stt = DeepgramSTTService(api_key=DEEPGRAM_API_KEY)
        llm = OpenAILLMService(api_key=OPENAI_API_KEY, model="gpt-4o-mini")
        tts = CartesiaTTSService(api_key=CARTESIA_API_KEY, voice_id=VOICE_ID)

        pipeline = Pipeline([transport.input(), stt, llm, tts, transport.output()])
        task = PipelineTask(
            pipeline,
            params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        )

        attach(task, project="acme")

        runner = PipelineRunner()
        await runner.run(task)
    ```
  </Tab>
</Tabs>

If you also need per-end-user attribution within the Acme project, add `tenant_id=` as well:

```python
attach(session, project="acme", tenant_id=caller_id)
```

## Step 4: upload client branding

Open the dashboard at `http://127.0.0.1:8080/projects`, find the `acme` card, click **Brand**, and fill the modal:

- **Product name**: e.g. `AcmeVoice` (up to 64 characters; replaces "VoiceGateway" in the sidebar).
- **Accent color**: any valid hex, e.g. `#FF6633`.
- **Logo**: PNG or SVG, max 256 KB. PNG max 512x512 px.

For scripted provisioning, the `brand` CLI subgroup hits the same endpoint:

```bash
voicegw brand set \
  --project acme \
  --logo ./acme-logo.png \
  --accent "#FF6633" \
  --name AcmeVoice
# Project acme branding updated:
#   Logo:         /static/branding/uploads/acme.png
#   Accent color: #FF6633
#   Product name: AcmeVoice
```

Set `VOICEGW_API_KEY` when your dashboard requires auth.

### What the logo upload accepts

Uploaded logos are served from the deployment's own origin, so the endpoint is strict about what it stores:

- **Admin scope.** `POST /api/projects/{id}/branding/logo` requires an admin-scoped token once you have configured API keys. A read-scoped token gets `403`. With no API keys configured (the single-operator default) nothing changes.
- **Uploads land in `/static/branding/uploads/`,** never in the branding root, so an upload cannot replace one of the assets that ship with the dashboard. The filename comes from the project id when the id is plain (letters, digits, `_`, `-`); any other id is replaced by a digest of itself, so the stored path is stable but can never contain a path separator.
- **SVGs are inspected and refused, not sanitized.** A `<script>` element, an `on*` event-handler attribute, `<foreignObject>`, `<iframe>`, `<embed>`, `<object>`, SMIL `<animate>`/`<set>`, an entity declaration, or a `javascript:` or `data:` URL in any attribute returns `400` with the reason. The file must also parse as well-formed XML with an `<svg>` root. Export the logo as a plain vector (paths, groups, fills) or upload the PNG instead.
- **Every asset under `/static/branding` is served with `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; sandbox` and `X-Content-Type-Options: nosniff`,** so a branding file cannot execute in the dashboard's origin even if it gets past the content check. This does not affect how the logo renders in the dashboard.

## Step 5: share a branded dashboard link

Branding is per-project. Share `https://your-gateway/sessions?project=acme` with the client and they see the AcmeVoice brand: sidebar logo, accent color on interactive elements, page title and favicon. Without `?project=acme` the default VoiceGateway brand renders.

## Step 6: monitor per-client cost and routing

Open `/routing` in the dashboard. The page shows per-provider p50/p95 and sample count per project. Sort by p50 ascending to find the fastest provider in each modality, or by sample count to gauge confidence.

From the Sessions page, click any row to open the SessionDetail modal. The routing strip shows:

- STT, LLM, and TTS picked for that session.
- The latency budget in effect at session start.
- Actual end-to-end latency when the close-session hook has populated `budget_ms_used`.
- A yellow `budget_overrun` chip when the router fell back to fastest because nothing fit the budget.

## Known limitations

- **No mid-call routing.** Provider is chosen at session start. Mid-call degradation keeps the original provider.
- **No per-tenant branding inside one project.** White-label is per-project only.
- **No cost-aware routing.** The router picks on latency; cost is tracked but does not feed back into the picker.
- **No multi-region routing.**
- **No custom-domain dashboard hosting.** White-label sits at the gateway's own host.

## See also

<CardGroup>
  <Card title="Configuration: projects" href="/configuration/projects">
    Full projects schema: budgets, rosters, and stale-key settings.
  </Card>
  <Card title="Tenant attribution" href="/guide/multi-tenant-quickstart">
    Stamp per-end-user cost attribution within a project on the wire.
  </Card>
  <Card title="Budget enforcement example" href="/examples/budget-enforcement">
    Code example for daily_budget enforcement with guard().
  </Card>
  <Card title="Cost reconciliation" href="/guide/cost-reconciliation">
    Verify recorded per-project totals against provider invoices.
  </Card>
</CardGroup>
