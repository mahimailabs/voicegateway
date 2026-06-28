---
title: First agent
description: Build a working LiveKit voice agent that routes STT / LLM / TTS through VoiceGateway. Costs and latency land in the dashboard automatically.
---

# First agent

This guide walks through building a voice AI agent using VoiceGateway
with [LiveKit Agents](https://docs.livekit.io/agents/). By the end
you have a working agent that listens, thinks, and speaks using
providers configured through VoiceGateway.

## Prerequisites

- Python 3.11+
- VoiceGateway installed with cloud providers:
  `pipx install 'voicegateway[cloud,dashboard]'`
- LiveKit Agents SDK: `pip install livekit-agents`
- API keys for at least one STT, LLM, and TTS provider
- A LiveKit server (Cloud or self-hosted): setup walkthrough below

## LiveKit server setup

A LiveKit Agents worker connects to a LiveKit server over WebSocket.
You have two options.

### Option A: LiveKit Cloud (free tier)

1. Sign up at [livekit.io](https://livekit.io/).
2. Create a project from the Cloud dashboard.
3. On the project's settings page, copy the WebSocket URL and the
   API key + secret pair.

### Option B: self-hosted `livekit-server` (local development)

The fastest path on your laptop is the official Docker image with
the development flag:

```bash
docker run --rm \
  -p 7880:7880 \
  -p 7881:7881 \
  -p 7882:7882/udp \
  livekit/livekit-server --dev
```

The `--dev` flag uses default credentials: API key `devkey`, API
secret `secret`. For a production self-hosted setup, follow the
[LiveKit self-hosting guide](https://docs.livekit.io/home/self-hosting/local/).

### Export credentials

Set three environment variables that `livekit-agents` reads at
startup:

```bash
# LiveKit Cloud
export LIVEKIT_URL=wss://<your-project>.livekit.cloud
export LIVEKIT_API_KEY=<your-key>
export LIVEKIT_API_SECRET=<your-secret>

# Self-hosted local --dev
export LIVEKIT_URL=ws://localhost:7880
export LIVEKIT_API_KEY=devkey
export LIVEKIT_API_SECRET=secret
```

A worker started without these env vars fails with
`ConnectionError: Failed to connect`. Verify they are set:

```bash
echo "LIVEKIT_URL=$LIVEKIT_URL"
echo "LIVEKIT_API_KEY=$LIVEKIT_API_KEY"
echo "LIVEKIT_API_SECRET=$LIVEKIT_API_SECRET"
```

## Step 1: configure VoiceGateway

Create or update your `voicegw.yaml`:

```yaml
projects:
  my-agent:
    name: My First Agent
    description: A demo voice agent
    daily_budget: 5.00
    budget_action: warn
    tags: [dev]
    providers:
      deepgram:
        api_key: ${DEEPGRAM_API_KEY}
      anthropic:
        api_key: ${ANTHROPIC_API_KEY}
      cartesia:
        api_key: ${CARTESIA_API_KEY}

default_project: my-agent

cost_tracking:
  enabled: true

observability:
  latency_tracking: true
  cost_tracking: true
  request_logging: true
```

Export your API keys:

```bash
export DEEPGRAM_API_KEY="your-key"
export ANTHROPIC_API_KEY="your-key"
export CARTESIA_API_KEY="your-key"
```

## Step 2: write the agent

Create `agent.py`:

```python
from livekit.agents import AgentSession, Agent

from voicegateway.inference import STT, LLM, TTS


class MyAgent(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are a helpful voice assistant. Keep responses concise."
            ),
        )


async def entrypoint(ctx) -> None:
    await ctx.connect()

    # default_project: my-agent in voicegw.yaml means the inference
    # factories pick up my-agent's per-project keys without any extra
    # call here. Use set_project(...) from voicegateway.core.active_project
    # to override per request.
    session = AgentSession(
        stt=STT("deepgram/nova-3"),
        llm=LLM("anthropic/claude-sonnet-4-5"),
        tts=TTS("cartesia/sonic-3"),
    )

    await session.start(
        agent=MyAgent(),
        room=ctx.room,
    )
```

## Step 3: run the agent

```bash
python agent.py
```

The agent connects to your LiveKit room and begins listening.
VoiceGateway routes STT requests to Deepgram, LLM requests to
Anthropic, and TTS requests to Cartesia. Cost tracking and latency
monitoring happen automatically.

## Step 4: monitor with the dashboard

```bash
voicegw dashboard
```

That opens your browser at the daemon URL (default
`http://127.0.0.1:8080`). The Costs page shows live cost tracking
per provider and per model, the Latency page shows p50 / p95 / p99
per model, and the Logs page shows the request stream for your
agent.

## Routing to a different project

The agent above relies on `default_project: my-agent` in YAML. When
one process serves multiple agents, switch per request context with
`set_project`:

```python
from voicegateway.core.active_project import set_project
from voicegateway.inference import STT

# Inside one async task
set_project("tony-pizza")
stt = STT("deepgram/nova-3")  # uses tony-pizza's key

# A separate asyncio.Task gets its own context, so no leakage.
```

## Adding fallbacks

For resolver-time fallback (try the next model in the chain when
the primary fails at startup) walk a chain manually using the
inference factories: iterate the `fallbacks.<modality>` list from
`voicegw.yaml` and use the first model whose provider plugin imports
cleanly.

```python
from voicegateway.inference import LLM

for model_id in ["openai/gpt-4.1-mini", "anthropic/claude-sonnet-4-5"]:
    try:
        llm = LLM(model_id)
        break
    except ImportError:
        continue
```

## Next steps

- [Core concepts](/guide/core-concepts): understand gateways,
  stacks, projects, and fallbacks.
- [Configuration reference](/configuration/voicegw-yaml): full
  YAML reference.
- [Projects](/configuration/projects): per-project budgets and
  tracking.
- [Providers](/configuration/providers): details on every
  supported provider.
