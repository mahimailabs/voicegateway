# First Agent

This guide walks through building a voice AI agent using VoiceGateway with [LiveKit Agents](https://docs.livekit.io/agents/). By the end you will have a working agent that listens, thinks, and speaks using providers configured through VoiceGateway.

## Prerequisites

- Python 3.11+
- VoiceGateway installed with cloud providers: `pip install voicegateway[cloud]`
- LiveKit Agents SDK: `pip install livekit-agents`
- API keys for at least one STT, LLM, and TTS provider
- A LiveKit server (local or cloud) -- see [LiveKit docs](https://docs.livekit.io/home/get-started/)

## Step 1: Configure VoiceGateway

Create or update your `voicegw.yaml`:

```yaml
providers:
  deepgram:
    api_key: ${DEEPGRAM_API_KEY}
  anthropic:
    api_key: ${ANTHROPIC_API_KEY}
  cartesia:
    api_key: ${CARTESIA_API_KEY}

stacks:
  premium:
    stt: deepgram/nova-3
    llm: anthropic/claude-sonnet-4-20250514
    tts: cartesia/sonic-3

fallbacks:
  stt:
    - deepgram/nova-3
    - openai/whisper-1
  llm:
    - anthropic/claude-sonnet-4-20250514
    - openai/gpt-4.1-mini

projects:
  my-agent:
    name: My First Agent
    description: A demo voice agent
    default_stack: premium
    daily_budget: 5.00
    budget_action: warn
    tags: [dev]

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
from voicegateway import Gateway

# Initialize the gateway
gw = Gateway()

# Resolve all three models from the "premium" stack
stt, llm, tts = gw.stack("premium", project="my-agent")


class MyAgent(Agent):
    def __init__(self):
        super().__init__(
            instructions="You are a helpful voice assistant. Keep responses concise.",
        )


async def entrypoint(ctx):
    await ctx.connect()

    session = AgentSession(
        stt=stt,
        llm=llm,
        tts=tts,
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

## Using fallbacks instead of stacks

If you prefer automatic failover over explicit model selection, use fallback methods:

```python
stt = gw.stt_with_fallback(project="my-agent")
llm = gw.llm_with_fallback(project="my-agent")
tts = gw.tts_with_fallback(project="my-agent")
```

VoiceGateway will try each provider in the fallback chain until one succeeds.

## Using individual models

You can also select models directly without stacks:

```python
stt = gw.stt("deepgram/nova-3", project="my-agent")
llm = gw.llm("anthropic/claude-sonnet-4-20250514", project="my-agent")
tts = gw.tts("cartesia/sonic-3", project="my-agent")
```

## Next steps

- [Core Concepts](/guide/core-concepts) -- understand gateways, stacks, projects, and fallbacks
- [Configuration Reference](/configuration/voicegw-yaml) -- full YAML reference
- [Projects](/configuration/projects) -- per-project budgets and tracking
- [Providers](/configuration/providers) -- details on all 11 providers
