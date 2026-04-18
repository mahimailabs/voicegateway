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
providers:
  openai:
    api_key: ${OPENAI_API_KEY}
  deepgram:
    api_key: ${DEEPGRAM_API_KEY}
  cartesia:
    api_key: ${CARTESIA_API_KEY}

models:
  stt:
    deepgram/nova-3:
      provider: deepgram
      model: nova-3
  llm:
    openai/gpt-4.1-mini:
      provider: openai
      model: gpt-4.1-mini
  tts:
    cartesia/sonic-3:
      provider: cartesia
      model: sonic-3
      default_voice: 794f9389-aac1-45b6-b726-9d9369183238

cost_tracking:
  enabled: true

observability:
  latency_tracking: true
```

## Basic Usage

```python
from voicegateway import Gateway

# Initialize the gateway (auto-discovers voicegw.yaml)
gw = Gateway()

# Create provider instances -- these are LiveKit-compatible
stt = gw.stt("deepgram/nova-3")
llm = gw.llm("openai/gpt-4.1-mini")
tts = gw.tts("cartesia/sonic-3")

# These instances work exactly like direct LiveKit plugin instances
# but with automatic cost tracking, latency monitoring, and logging
```

## LiveKit Agent Integration

```python
from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli, llm as lk_llm
from livekit.agents.voice_assistant import VoiceAssistant
from voicegateway import Gateway

gw = Gateway()


async def entrypoint(ctx: JobContext):
    # Connect to the LiveKit room
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    # Create the voice pipeline through VoiceGateway
    stt = gw.stt("deepgram/nova-3", project="voice-agent")
    llm = gw.llm("openai/gpt-4.1-mini", project="voice-agent")
    tts = gw.tts("cartesia/sonic-3", project="voice-agent")

    # Set up the system prompt
    initial_ctx = lk_llm.ChatContext()
    initial_ctx.append(
        role="system",
        text="You are a helpful voice assistant. Be concise in your responses.",
    )

    # Create and start the voice assistant
    assistant = VoiceAssistant(
        vad=ctx.proc.userdata.get("vad"),
        stt=stt,
        llm=llm,
        tts=tts,
        chat_ctx=initial_ctx,
    )
    assistant.start(ctx.room)
    await assistant.say("Hello! How can I help you today?")


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
```

## Using Stacks

Instead of specifying each model separately, define named stacks:

```yaml
stacks:
  premium:
    stt: deepgram/nova-3
    llm: openai/gpt-4.1-mini
    tts: cartesia/sonic-3
  budget:
    stt: deepgram/nova-3
    llm: groq/llama-3.3-70b-versatile
    tts: elevenlabs/turbo-v2.5
```

```python
gw = Gateway()

# Resolve a complete stack in one call
stt, llm, tts = gw.stack("premium", project="voice-agent")
```

## Checking Costs

After running your agent, query costs from Python or the CLI:

```python
# From Python
costs = gw.costs(period="today", project="voice-agent")
print(f"Today's cost: ${costs['total']:.4f}")
print(f"By provider: {costs['by_provider']}")
print(f"By model: {costs['by_model']}")
```

```bash
# From the CLI
voicegw status

# Or start the dashboard
voicegw dashboard
# Open http://localhost:9090
```

## Monitoring Latency

VoiceGateway automatically records TTFB and total latency for every request. View these metrics through the dashboard or the HTTP API:

```bash
curl http://localhost:8080/v1/metrics?period=today
```

The `latency.ttfb_warning_ms` config value (default 500ms) triggers a log warning when TTFB exceeds the threshold -- useful for catching provider degradation early.

## What Happens Under the Hood

When you call `gw.stt("deepgram/nova-3", project="voice-agent")`:

1. The Gateway checks the project's budget (if configured)
2. The Router parses `"deepgram/nova-3"` into `ModelId(provider="deepgram", model="nova-3")`
3. The Registry lazily imports and instantiates `DeepgramProvider`
4. The provider creates a LiveKit-compatible `deepgram.STT` instance
5. The Gateway wraps it in `InstrumentedSTT` to track latency and cost
6. You get back an object that behaves exactly like a `deepgram.STT` instance

All of this is transparent -- your LiveKit Agent code sees the same API surface whether it uses VoiceGateway or direct plugin imports.
