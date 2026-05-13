# First Agent

This guide walks through building a voice AI agent using VoiceGateway with [LiveKit Agents](https://docs.livekit.io/agents/). By the end you will have a working agent that listens, thinks, and speaks using providers configured through VoiceGateway.

## Prerequisites

- Python 3.11+
- VoiceGateway installed with cloud providers: `pip install voicegateway[cloud]`
- LiveKit Agents SDK: `pip install livekit-agents`
- API keys for at least one STT, LLM, and TTS provider
- A LiveKit server (Cloud or self-hosted): setup walkthrough below

## LiveKit Server Setup

A LiveKit Agents worker connects to a LiveKit server over WebSocket. You have two options.

### Option A: LiveKit Cloud (free tier)

1. Sign up at [livekit.io](https://livekit.io/).
2. Create a project from the Cloud dashboard.
3. On the project's settings page, copy the WebSocket URL and the API key + secret pair.

### Option B: self-hosted `livekit-server` (local development)

The fastest path on your laptop is the official Docker image with the development flag:

```bash
docker run --rm \
  -p 7880:7880 \
  -p 7881:7881 \
  -p 7882:7882/udp \
  livekit/livekit-server --dev
```

The `--dev` flag uses default credentials: API key `devkey`, API secret `secret`. For a production self-hosted setup, follow the [LiveKit self-hosting guide](https://docs.livekit.io/home/self-hosting/local/).

### Export credentials

Set three environment variables that `livekit-agents` reads at startup:

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

A worker started without these env vars fails with `ConnectionError: Failed to connect`. Verify they are set:

```bash
echo "LIVEKIT_URL=$LIVEKIT_URL"
echo "LIVEKIT_API_KEY=$LIVEKIT_API_KEY"
echo "LIVEKIT_API_SECRET=$LIVEKIT_API_SECRET"
```

If the values print and are non-empty, you are ready for Step 1.

## Step 1: Configure VoiceGateway

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

## Step 2: Write the agent

Create `agent.py`:

```python
from livekit.agents import AgentSession, Agent, RoomInputOptions
from livekit.agents.llm import ChatContext
from voicegateway import inference


class MyAgent(Agent):
    def __init__(self):
        super().__init__(
            instructions="You are a helpful voice assistant. Keep responses concise.",
        )


async def entrypoint(ctx):
    await ctx.connect()

    # default_project: my-agent in voicegw.yaml means the inference
    # factories pick up my-agent's per-project keys without any extra
    # call here. Use inference.set_project("...") to override.
    session = AgentSession(
        stt=inference.STT("deepgram/nova-3"),
        llm=inference.LLM("anthropic/claude-sonnet-4-20250514"),
        tts=inference.TTS("cartesia/sonic-3"),
    )

    await session.start(
        agent=MyAgent(),
        room=ctx.room,
    )
```

## Step 3: Run the agent

```bash
python agent.py
```

<!-- TODO: screenshot of agent running -->

The agent connects to your LiveKit room and begins listening. VoiceGateway routes STT requests to Deepgram, LLM requests to Anthropic, and TTS requests to Cartesia. Cost tracking and latency monitoring happen automatically.

## Step 4: Monitor with the dashboard

In a separate terminal:

```bash
voicegw dashboard
```

Open `http://localhost:9090` in your browser to see live cost tracking, latency percentiles, and request logs for your agent.

<!-- TODO: screenshot of dashboard -->

## Routing to a different project

The agent above relies on `default_project: my-agent` in YAML. When one process serves multiple agents, switch per call context with `inference.set_project`:

```python
from voicegateway import inference

# Inside one async task
inference.set_project("tony-pizza")
stt = inference.STT("deepgram/nova-3")  # uses tony-pizza's key

# A separate asyncio.Task gets its own context, so no leakage.
```

## Adding fallbacks

For resolver-time fallback (try the next model in the chain when the primary fails at startup) walk a chain manually using the inference factories — iterate the `fallbacks.<modality>` list from `voicegw.yaml` and use the first model whose provider plugin imports cleanly. v0.0.6 will add a first-class `fallback=` parameter to the `inference` factories.

## Next steps

- [Core Concepts](/guide/core-concepts) -- understand gateways, stacks, projects, and fallbacks
- [Configuration Reference](/configuration/voicegw-yaml) -- full YAML reference
- [Projects](/configuration/projects) -- per-project budgets and tracking
- [Providers](/configuration/providers) -- details on all 11 providers
