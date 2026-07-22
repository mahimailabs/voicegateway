---
title: Basic Voice Agent
description: Build a minimal metered voice agent using LiveKit or Pipecat with VoiceGateway cost tracking.
---

# Basic Voice Agent

The minimal setup to get a working, metered voice pipeline. Use `attach(session)` to add cost tracking to a session you build yourself with native provider plugins.

## Install

<CodeGroup>
```bash uv
uv add "voicegateway[livekit]"
uv add livekit-agents livekit-plugins-deepgram livekit-plugins-openai livekit-plugins-cartesia
```

```bash pip
pip install "voicegateway[livekit]"
pip install livekit-agents livekit-plugins-deepgram livekit-plugins-openai livekit-plugins-cartesia
```
</CodeGroup>

VoiceGateway is framework-agnostic and no longer bundles provider wheels. Install the provider plugins your agent uses (you likely already have them), and VoiceGateway meters them by model_id via voice-prices.

## Configuration

Create `voicegw.yaml` in your project root:

```yaml
projects:
  voice-agent:
    name: Voice Agent
    daily_budget: 5.00
    budget_action: warn
    providers:
      openai:
        api_key: ${OPENAI_API_KEY}
      deepgram:
        api_key: ${DEEPGRAM_API_KEY}
      cartesia:
        api_key: ${CARTESIA_API_KEY}

default_project: voice-agent

cost_tracking:
  enabled: true

observability:
  latency_tracking: true
```

## Agent code

<Tabs>
  <Tab title="LiveKit">
```python
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import cartesia, deepgram, openai, silero
from voicegateway import attach


async def entrypoint(ctx: JobContext):
    await ctx.connect()

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=deepgram.STT(model="nova-3"),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=cartesia.TTS(model="sonic-3"),
    )

    # Single passive meter: records cost, latency, and session id.
    attach(session, project="voice-agent")

    await session.start(
        agent=Agent(
            instructions="You are a helpful voice assistant. Be concise.",
        ),
        room=ctx.room,
    )


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
```
  </Tab>
  <Tab title="Pipecat">
```python
import os

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openai.llm import OpenAILLMService
from voicegateway import attach


def build_task(transport_input, transport_output):
    stt = DeepgramSTTService(api_key=os.environ["DEEPGRAM_API_KEY"])
    llm = OpenAILLMService(api_key=os.environ["OPENAI_API_KEY"], model="gpt-4o-mini")
    tts = CartesiaTTSService(api_key=os.environ["CARTESIA_API_KEY"])

    pipeline = Pipeline([transport_input, stt, llm, tts, transport_output])

    task = PipelineTask(
        pipeline,
        # Pipecat must emit usage frames for attach() to record them.
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
    )

    # Attach the single passive meter after task construction.
    attach(task, project="voice-agent")
    return task
```
  </Tab>
</Tabs>

## Run

```bash
python agent.py start
```

The standard `livekit-agents` worker CLI connects to your LiveKit server. Costs and latency land in the dashboard under the `voice-agent` project.

## Check costs

```bash
voicegw costs --project voice-agent
voicegw logs  --project voice-agent
voicegw dashboard
```

Or via the HTTP API:

```bash
curl 'http://localhost:8080/v1/costs?period=today&project=voice-agent'
```

## Notes

- `attach()` is passive: it measures but never reroutes traffic.
- To add fallback or a spend cap, wrap the LLM with [`guard()`](/guide/guard) before passing it to `AgentSession`.
- For the full `attach` + `guard` worked example, see [LiveKit: attach + guard](/examples/livekit-attach-guard).
