---
title: First agent
description: A complete, runnable LiveKit voice agent using attach() for cost metering and guard() for an LLM fallback and spend cap.
---
One runnable LiveKit agent file. Copy it, set your keys, run it.

## Prerequisites

- Python 3.11 or later
- VoiceGateway installed for LiveKit (see [Installation](/guide/installation))
- A LiveKit server: a LiveKit Cloud project, or `livekit-server --dev` locally
- API keys for Deepgram, OpenAI, and Cartesia (swap in your own providers)

## Install

<CodeGroup>
```bash uv
uv pip install "voicegateway[livekit]"
uv pip install livekit-agents livekit-plugins-openai livekit-plugins-deepgram livekit-plugins-cartesia
```
```bash pip
pip install "voicegateway[livekit]"
pip install livekit-agents livekit-plugins-openai livekit-plugins-deepgram livekit-plugins-cartesia
```
</CodeGroup>

VoiceGateway is framework-agnostic and does not bundle provider wheels. You
install the LiveKit plugins your agent uses, and VoiceGateway meters them by
`model_id` through `voice-prices`.

## Set environment variables

```bash
export LIVEKIT_URL=wss://your-project.livekit.cloud
export LIVEKIT_API_KEY=your-livekit-key
export LIVEKIT_API_SECRET=your-livekit-secret

export DEEPGRAM_API_KEY=your-deepgram-key
export OPENAI_API_KEY=your-openai-key
export CARTESIA_API_KEY=your-cartesia-key
```

For local development with `livekit-server --dev`, use:

```bash
export LIVEKIT_URL=ws://localhost:7880
export LIVEKIT_API_KEY=devkey
export LIVEKIT_API_SECRET=secret
```

The worker exits at startup without `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and
`LIVEKIT_API_SECRET` set.

## agent.py

```python
"""LiveKit voice agent with VoiceGateway cost metering and LLM fallback."""

from livekit.agents import Agent, AgentSession, WorkerOptions, cli
from livekit.plugins import cartesia, deepgram, openai

import voicegateway


class MyAgent(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are a friendly voice assistant. Keep your answers short "
                "and clear. One or two sentences unless asked for more."
            ),
        )


async def entrypoint(ctx) -> None:
    await ctx.connect()

    # guard() wraps the LLM with a fallback and a daily budget.
    # It returns a drop-in openai.LLM, so AgentSession sees no difference.
    guarded_llm = voicegateway.guard(
        openai.LLM(model="gpt-4o-mini"),
        fallback=[openai.LLM(model="gpt-4o")],
        rate_limit="60/min",
        budget="$5.00/day",
        project="my-agent",
    )

    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        llm=guarded_llm,
        tts=cartesia.TTS(model="sonic-3"),
    )

    # attach() is the single meter. Call it once, before session.start().
    voicegateway.attach(session, project="my-agent")

    await session.start(agent=MyAgent(), room=ctx.room)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
```

## Run the agent

```bash
python agent.py dev
```

Connect from a browser or the [LiveKit Playground](https://playground.livekit.io),
say something, and watch the dashboard.

## What happens

1. `deepgram.STT` transcribes speech. `attach()` meters audio minutes and cost.
2. `guard(openai.LLM(...))` sends the transcript to GPT-4o mini. On an error,
   `guard()` retries with GPT-4o automatically. `attach()` meters prompt
   tokens, completion tokens, and cost.
3. `cartesia.TTS` synthesizes speech. `attach()` meters characters and cost.
4. Every row lands in the dashboard at `http://localhost:8080`.

## View costs

```bash
voicegw dashboard              # opens the browser at http://localhost:8080
voicegw costs --project my-agent
voicegw logs  --project my-agent
voicegw status
```

## Notes

- `attach()` alone, without `guard()`, is a complete and valid setup: you get
  cost and latency tracking with no change to how the call behaves. Reach for
  `guard()` only on the providers where you want fallback, a rate limit, or a
  spend cap.
- `guard()` returns the same type it wraps, so `guarded_llm` slots into
  `AgentSession` exactly like a plain `openai.LLM`.

## Next steps

- [attach()](/guide/attach): full signature, including `tenant_id` for multi-tenant attribution.
- [guard()](/guide/guard): full signature, including per-framework fallback scope.
- [Frameworks and extras](/guide/frameworks): the framework-neutral core.
- [Configuration reference](/configuration/voicegw-yaml): every YAML key.
- [Providers](/configuration/providers): all supported providers and model IDs.
