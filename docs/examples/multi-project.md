---
title: Multi-Project Setup
description: Attribute costs across multiple projects by passing project= to attach() for per-project isolation.
---

# Multi-Project Setup

Configure multiple projects with different model stacks, budgets, and tracking. Useful when separate teams, environments, or products share a single VoiceGateway instance.

Pass `project=` to `attach()` (and optionally to `guard()`) to isolate cost tracking per session.

## Configuration

```yaml
projects:
  prod:
    name: Production
    description: Customer-facing voice agents
    daily_budget: 50.00
    budget_action: throttle
    tags: [production, customer-facing]
    providers:
      openai:
        api_key: ${PROD_OPENAI_KEY}
      deepgram:
        api_key: ${PROD_DEEPGRAM_KEY}
      cartesia:
        api_key: ${PROD_CARTESIA_KEY}

  staging:
    name: Staging
    description: Pre-release testing environment
    daily_budget: 10.00
    budget_action: warn
    tags: [staging, testing]
    providers:
      openai:
        api_key: ${STAGING_OPENAI_KEY}
      deepgram:
        api_key: ${STAGING_DEEPGRAM_KEY}
      cartesia:
        api_key: ${STAGING_CARTESIA_KEY}

  dev:
    name: Development
    description: Developer sandbox
    daily_budget: 5.00
    budget_action: block
    tags: [development]
    # dev uses local providers (no api_key needed; ollama and friends)
    # read from the top-level providers: block.

providers:
  ollama:
    base_url: http://localhost:11434
  whisper: {}
  kokoro: {}

cost_tracking:
  enabled: true
```

## Using projects in code

Pass `project=` directly to `attach()` so the active project is explicit on every session:

<Tabs>
  <Tab title="LiveKit">
```python
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import cartesia, deepgram, openai, silero
from voicegateway import attach


async def production_entrypoint(ctx: JobContext):
    await ctx.connect()

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=deepgram.STT(model="nova-3"),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=cartesia.TTS(model="sonic-3"),
    )

    # project= is explicit; no global state, no leakage between concurrent tasks.
    attach(session, project="prod")

    await session.start(
        agent=Agent(instructions="You are a helpful voice assistant."),
        room=ctx.room,
    )


async def staging_entrypoint(ctx: JobContext):
    await ctx.connect()

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=deepgram.STT(model="nova-3"),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=cartesia.TTS(model="sonic-3"),
    )

    attach(session, project="staging")

    await session.start(
        agent=Agent(instructions="You are a helpful voice assistant."),
        room=ctx.room,
    )
```
  </Tab>
  <Tab title="Pipecat">
```python
import os

import voicegateway
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openai.llm import OpenAILLMService


def build_task(transport_input, transport_output, *, project: str):
    stt = DeepgramSTTService(api_key=os.environ["DEEPGRAM_API_KEY"])
    llm = OpenAILLMService(api_key=os.environ["OPENAI_API_KEY"], model="gpt-4o-mini")
    tts = CartesiaTTSService(api_key=os.environ["CARTESIA_API_KEY"])

    pipeline = Pipeline([transport_input, stt, llm, tts, transport_output])

    # project= passed through; each pipeline task charges its own project.
    return PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[voicegateway.Observer(project=project)],
    )


# Production pipeline
prod_task = build_task(transport_in, transport_out, project="prod")

# Staging pipeline
staging_task = build_task(transport_in, transport_out, project="staging")
```
  </Tab>
</Tabs>

<Note>
There is no `VOICEGW_PROJECT` env var. Pass `project=` explicitly to `attach()`
or `guard()` so the attribution is always unambiguous in code.
</Note>

## Querying per-project costs

### Via the CLI

```bash
voicegw costs --project prod
voicegw costs --project staging
voicegw projects   # shows budget + recent spend per project
```

### Via the HTTP API

```bash
# Per-project cost breakdown
curl 'http://localhost:8080/v1/costs?period=today&project=prod'

# All projects
curl http://localhost:8080/v1/projects

# Project-level request logs
curl 'http://localhost:8080/v1/logs?project=prod&limit=50'
```

## Budget behavior by project

| Project | Budget | Action | What happens when exceeded |
|---------|--------|--------|---------------------------|
| prod | $50/day | `throttle` | `guard()` falls back to cheaper model |
| staging | $10/day | `warn` | Logs a warning, request proceeds normally |
| dev | $5/day | `block` | `guard()` raises `BudgetExceededError` |

## Dynamic project management

Projects can also be created and updated at runtime through the dashboard or MCP server, without editing `voicegw.yaml`:

```bash
curl -X POST http://localhost:8080/v1/projects \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "demo",
    "name": "Demo Environment",
    "daily_budget": 2.00,
    "budget_action": "warn",
    "tags": ["demo"]
  }'
```

These dynamically created projects are stored in the `managed_projects` SQLite table and merged with YAML-defined projects at startup and after each write.

## SQL views for reporting

The `project_daily_costs` view aggregates costs by project and day:

```sql
SELECT project, day, SUM(total_cost) as cost
FROM project_daily_costs
WHERE day >= date('now', '-7 days')
GROUP BY project, day
ORDER BY project, day;
```

This is what the dashboard uses to render per-project cost charts.
