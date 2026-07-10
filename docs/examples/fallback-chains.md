---
title: Fallback Chains
description: Startup-time provider selection using guard() with fallback= so the first available provider wins.
---

# Fallback Chains

Use `guard(primary, fallback=[backup1, backup2])` to wire a chain of providers at startup. If the primary is unavailable or fails, `guard()` advances to the next provider in the list.

<Note>
Resolver-time fallback handles startup selection. Once a session is live,
providers are not swapped mid-call. For runtime failover during an active call,
see [LiveKit FallbackAdapter integration](/examples/livekit-fallback-adapter).
</Note>

## Configuration

```yaml
projects:
  prod:
    name: Production
    daily_budget: 50.00
    budget_action: warn
    providers:
      deepgram:
        api_key: ${DEEPGRAM_API_KEY}
      openai:
        api_key: ${OPENAI_API_KEY}
      cartesia:
        api_key: ${CARTESIA_API_KEY}
      elevenlabs:
        api_key: ${ELEVENLABS_API_KEY}
      groq:
        api_key: ${GROQ_API_KEY}

default_project: prod

cost_tracking:
  enabled: true
```

## Wiring chains with guard()

```python
from livekit.plugins import cartesia, deepgram, elevenlabs, openai
from livekit.plugins import groq as groq_plugin
from voicegateway import attach, guard


def build_session():
    from livekit.agents import AgentSession
    from livekit.plugins import silero

    return AgentSession(
        vad=silero.VAD.load(),
        stt=guard(
            deepgram.STT(model="nova-3"),
            fallback=[
                openai.STT(model="whisper-1"),
            ],
            project="prod",
        ),
        llm=guard(
            openai.LLM(model="gpt-4o-mini"),
            fallback=[
                groq_plugin.LLM(model="llama-3.3-70b-versatile"),
            ],
            project="prod",
        ),
        tts=guard(
            cartesia.TTS(model="sonic-3"),
            fallback=[
                elevenlabs.TTS(model="turbo-v2.5"),
            ],
            project="prod",
        ),
    )
```

`guard()` returns the same type as the primary, so it is a drop-in replacement
anywhere you would use the provider directly.

## Full LiveKit agent with fallback

```python
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import cartesia, deepgram, elevenlabs, openai, silero
from livekit.plugins import groq as groq_plugin
from voicegateway import attach, guard

PROJECT = "prod"


async def entrypoint(ctx: JobContext):
    await ctx.connect()

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=guard(
            deepgram.STT(model="nova-3"),
            fallback=[openai.STT(model="whisper-1")],
            project=PROJECT,
        ),
        llm=guard(
            openai.LLM(model="gpt-4o-mini"),
            fallback=[groq_plugin.LLM(model="llama-3.3-70b-versatile")],
            project=PROJECT,
        ),
        tts=guard(
            cartesia.TTS(model="sonic-3"),
            fallback=[elevenlabs.TTS(model="turbo-v2.5")],
            project=PROJECT,
        ),
    )

    attach(session, project=PROJECT)

    await session.start(
        agent=Agent(instructions="You are a helpful voice assistant."),
        room=ctx.room,
    )


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
```

## Cloud-to-local fallback strategy

A common pattern is cloud models as primaries with local models as the final fallback. This keeps the agent alive even if every cloud provider is unreachable:

```python
from livekit.plugins import cartesia, deepgram, openai
from voicegateway import guard

# Local fallback imports come from the voicegateway providers, not livekit.plugins.
# Use a guard() chain ending in whichever local provider you have configured.
stt = guard(
    deepgram.STT(model="nova-3"),
    fallback=[openai.STT(model="whisper-1")],
    project="prod",
)
```

```yaml
# voicegw.yaml: document your fallback intention alongside the config
fallbacks:
  stt:
    - deepgram/nova-3
    - openai/whisper-1
    - local/whisper-large-v3
  llm:
    - openai/gpt-4o-mini
    - groq/llama-3.3-70b-versatile
    - ollama/qwen2.5:3b
  tts:
    - cartesia/sonic-3
    - elevenlabs/turbo-v2.5
    - local/kokoro
```

<Tip>
Anchor every chain with a local model as the last entry. A single local fallback
gives you true outage coverage at the cost of degraded quality during the
outage.
</Tip>

This handles the cold-start case: every cloud provider unreachable when the agent
starts means the local model is selected and the agent comes up. For warm failover
(a provider that starts returning errors mid-call), see [LiveKit FallbackAdapter
integration](/examples/livekit-fallback-adapter).
