# Basic Voice Agent

Build a voice agent with LiveKit Agents using VoiceGateway to route STT, LLM, and TTS requests.

## Prerequisites

```bash
pip install voicegateway[openai,deepgram,cartesia]
pip install livekit-agents livekit-plugins-deepgram livekit-plugins-openai livekit-plugins-cartesia
```

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

## Basic Usage

```python
from voicegateway import inference

# Each factory returns a wrapped LiveKit plugin instance, ready to drop
# into AgentSession. Cost, latency, and session correlation happen
# transparently in the middleware.
stt = inference.STT("deepgram/nova-3")
llm = inference.LLM("openai/gpt-4.1-mini")
tts = inference.TTS("cartesia/sonic-3")
```

If you already build your own LiveKit plugins, use the session observer instead:

```python
from voicegateway import attach

session_id = attach(session)
```

## LiveKit Agent Integration

```python
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import silero
from voicegateway import inference


async def entrypoint(ctx: JobContext):
    await ctx.connect()

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=inference.STT("deepgram/nova-3"),
        llm=inference.LLM("openai/gpt-4.1-mini"),
        tts=inference.TTS("cartesia/sonic-3"),
    )

    await session.start(
        agent=Agent(
            instructions="You are a helpful voice assistant. Be concise in your responses.",
        ),
        room=ctx.room,
    )


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
```

## Multiple agents in one process

When one process serves multiple agents (e.g., one worker handling several entrypoints), set the active project per call context:

```python
from voicegateway import inference

async def restaurant_entrypoint(ctx):
    inference.set_project("restaurant-agent")
    # all inference factories below charge the restaurant-agent project
    ...

async def support_entrypoint(ctx):
    inference.set_project("support-agent")
    # sibling tasks each have their own ContextVar; no leakage
    ...
```

## Checking Costs

```bash
voicegw costs --project voice-agent
voicegw logs --project voice-agent

# Or open the dashboard in your browser (the daemon already serves it):
voicegw dashboard
# Default URL: http://localhost:8080
```

```bash
# From the HTTP API:
curl 'http://localhost:8080/v1/costs?period=today&project=voice-agent'
```

## Monitoring Latency

VoiceGateway automatically records TTFB and total latency for every request. View these metrics through the dashboard or the HTTP API:

```bash
curl http://localhost:8080/v1/metrics?period=today
```

The `latency.ttfb_warning_ms` config value (default 500ms) triggers a log warning when TTFB exceeds the threshold, useful for catching provider degradation early.

## What Happens Under the Hood

When you call `inference.STT("deepgram/nova-3")`:

1. The factory parses `"deepgram/nova-3"` into provider `"deepgram"` and model `"nova-3"`.
2. The active project is resolved (set_project / env / yaml default / `"default"`).
3. The provider's API key is looked up: per-project entry first, then top-level `providers:`.
4. The Registry lazily imports and instantiates `DeepgramProvider`.
5. The provider creates a `livekit.plugins.deepgram.STT` instance.
6. The instance is wrapped in `InstrumentedSTT` to track cost, latency, and the session id.
7. You get back an object that behaves exactly like the underlying LK plugin instance.

All of this is transparent: your LiveKit Agent code sees the same API surface whether it uses `voicegateway.inference` or direct plugin imports.
