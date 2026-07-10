---
title: "LiveKit: attach + guard"
description: Native LiveKit plugins metered by attach(session) with a guarded LLM providing fallback, rate limiting, and a daily spend cap.
---

# LiveKit: attach + guard

A minimal LiveKit agent built from native `livekit.plugins` providers, metered
by [`attach(session)`](/guide/attach) (the single passive meter) and
controlled by one [`guard()`](/guide/guard) wrapper around the
LLM (fallback, rate limit, and a daily spend cap).

The full runnable file lives at
[`examples/livekit_attach_guard.py`](https://github.com/mahimailabs/voicegateway/blob/main/examples/livekit_attach_guard.py).

## Install

<CodeGroup>
```bash uv
uv add "voicegateway[livekit,openai,deepgram,cartesia]"
```

```bash pip
pip install "voicegateway[livekit,openai,deepgram,cartesia]"
```
</CodeGroup>

```bash
export OPENAI_API_KEY=... DEEPGRAM_API_KEY=... CARTESIA_API_KEY=...
export LIVEKIT_URL=... LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=...
```

## The agent

```python
import voicegateway

PROJECT = "livekit-demo"


def build_session():
    from livekit.agents import AgentSession
    from livekit.plugins import cartesia, deepgram, openai

    return AgentSession(
        stt=deepgram.STT(model="nova-3"),
        # guard() wraps ONE provider for control; it returns a drop-in LLM.
        llm=voicegateway.guard(
            openai.LLM(model="gpt-4o-mini"),
            fallback=[openai.LLM(model="gpt-4o")],
            rate_limit="60/min",
            budget="$5.00/day",
            project=PROJECT,
        ),
        tts=cartesia.TTS(model="sonic-3"),
    )


async def entrypoint(ctx):
    from livekit.agents import Agent

    await ctx.connect()

    session = build_session()

    # One meter for the whole session. guard adds no second row.
    voicegateway.attach(session, project=PROJECT)

    await session.start(
        agent=Agent(instructions="You are a helpful voice assistant. Be concise."),
        room=ctx.room,
    )
```

## Run

```bash
python examples/livekit_attach_guard.py start
```

That is the standard `livekit-agents` worker CLI; it needs a running LiveKit
server and the credentials above. Costs and latency land in the dashboard under
the `livekit-demo` project.

## Notes

- `attach()` is passive: it measures, never reroutes. `guard()` is active: it
  reroutes, throttles, and blocks. See
  [passive vs active](/guide/guard#passive-vs-active).
- The `livekit.plugins` imports are inside `build_session()` so importing the
  module never requires the plugin wheels; only running the agent does.
- For the Pipecat version, see
  [Pipecat: attach + guard](/examples/pipecat-attach-guard).
