# Migrating from LiveKit Cloud Inference

LiveKit Cloud offers a hosted inference service for STT, LLM, and TTS. VoiceGateway provides the same routing capabilities but runs entirely on your infrastructure, uses your own API keys, and adds cost tracking, budget enforcement, and an MCP server.

## Why migrate

| Concern | LiveKit Cloud Inference | VoiceGateway |
|---|---|---|
| Hosting | LiveKit-managed | Self-hosted (your servers, your VPC) |
| API keys | Managed by LiveKit | Your own keys, full control |
| Cost visibility | LiveKit billing | Per-project, per-model, per-modality dashboards |
| Budget enforcement | -- | Daily budgets with warn or block actions |
| Provider selection | LiveKit-supported set | 11 providers including local models |
| Local models | -- | Whisper, Kokoro, Piper, Ollama (zero-cost inference) |
| MCP integration | -- | 17 tools for managing the gateway from coding agents |
| Data residency | LiveKit regions | Wherever you deploy |
| Vendor lock-in | LiveKit platform | MIT-licensed, swap providers freely |

## API comparison

### LiveKit Cloud Inference

```python
from livekit.agents import llm, stt, tts
from livekit.plugins import openai, deepgram, cartesia

# Each plugin is configured independently
my_stt = deepgram.STT(model="nova-3")
my_llm = openai.LLM(model="gpt-4o-mini")
my_tts = cartesia.TTS(model="sonic-3")
```

### VoiceGateway

```python
from voicegateway import Gateway

gw = Gateway()  # reads voicegw.yaml

# Same plugin instances, routed through the gateway
my_stt = gw.stt("deepgram/nova-3", project="voice-app")
my_llm = gw.llm("openai/gpt-4o-mini", project="voice-app")
my_tts = gw.tts("cartesia/sonic-3", project="voice-app")
```

The returned objects are LiveKit plugin instances. Your `AgentSession` pipeline code does not change.

### Using in an agent pipeline

```python
from livekit.agents import AgentSession

session = AgentSession(
    stt=gw.stt("deepgram/nova-3", project="voice-app"),
    llm=gw.llm("openai/gpt-4o-mini", project="voice-app"),
    tts=gw.tts("cartesia/sonic-3", project="voice-app"),
)
```

This is identical to how you would wire LiveKit Cloud Inference, except `gw.stt()` / `gw.llm()` / `gw.tts()` add cost tracking, budget checks, and fallback chains transparently.

## Cost comparison

With LiveKit Cloud Inference, you pay LiveKit's per-unit pricing. With VoiceGateway, you pay the underlying providers directly. Example monthly cost for a moderate workload (10,000 minutes STT, 5M LLM tokens, 2M TTS characters):

| Component | LiveKit Cloud (estimated) | VoiceGateway + direct keys |
|---|---|---|
| STT (Deepgram Nova-3) | Bundled in LiveKit pricing | $43.00 |
| LLM (GPT-4o-mini) | Bundled in LiveKit pricing | $3.75 |
| TTS (Cartesia Sonic-3) | Bundled in LiveKit pricing | $130.00 |
| VoiceGateway | -- | Free (MIT license) |
| **Total** | Varies by LiveKit plan | **$176.75** |

The exact savings depend on your LiveKit plan and volume. VoiceGateway eliminates the inference markup -- you pay provider prices directly.

## Step-by-step migration

### 1. Install VoiceGateway

```bash
pip install voicegateway[cloud,dashboard]
```

### 2. Get your own API keys

Sign up for accounts with each provider you use:

- [Deepgram](https://console.deepgram.com/) -- STT
- [OpenAI](https://platform.openai.com/) -- LLM / STT / TTS
- [Anthropic](https://console.anthropic.com/) -- LLM
- [Cartesia](https://play.cartesia.ai/) -- TTS
- [ElevenLabs](https://elevenlabs.io/) -- TTS

### 3. Create your config

```bash
voicegw init
```

Map your current provider usage to `voicegw.yaml`:

```yaml
providers:
  deepgram:
    api_key: ${DEEPGRAM_API_KEY}
  openai:
    api_key: ${OPENAI_API_KEY}
  cartesia:
    api_key: ${CARTESIA_API_KEY}

models:
  stt:
    deepgram/nova-3:
      provider: deepgram
      model: nova-3
  llm:
    openai/gpt-4o-mini:
      provider: openai
      model: gpt-4o-mini
  tts:
    cartesia/sonic-3:
      provider: cartesia
      model: sonic-3

fallbacks:
  stt:
    - openai/whisper-1
  llm:
    - anthropic/claude-3.5-sonnet
  tts:
    - elevenlabs/eleven_turbo_v2_5

projects:
  voice-app:
    name: Voice Application
    daily_budget: 50.00
    budget_action: warn

cost_tracking:
  enabled: true
```

### 4. Update your agent code

Replace direct plugin imports with Gateway calls:

```python
# Before (LiveKit Cloud)
from livekit.plugins import deepgram, openai, cartesia

stt = deepgram.STT(model="nova-3")
llm = openai.LLM(model="gpt-4o-mini")
tts = cartesia.TTS(model="sonic-3")

# After (VoiceGateway)
from voicegateway import Gateway

gw = Gateway()
stt = gw.stt("deepgram/nova-3", project="voice-app")
llm = gw.llm("openai/gpt-4o-mini", project="voice-app")
tts = gw.tts("cartesia/sonic-3", project="voice-app")
```

### 5. Add fallback chains

VoiceGateway can automatically fail over when a provider is down:

```yaml
fallbacks:
  stt:
    - openai/whisper-1        # if Deepgram is down
    - groq/whisper-large-v3   # if OpenAI is down too
  llm:
    - anthropic/claude-3.5-sonnet
  tts:
    - elevenlabs/eleven_turbo_v2_5
```

### 6. Start the dashboard

```bash
voicegw dashboard
```

Open `http://localhost:9090` to see cost breakdowns, latency percentiles, and request logs across all modalities.

### 7. (Optional) Add local model fallbacks

For zero-cost fallbacks or air-gapped deployments:

```yaml
providers:
  ollama:
    base_url: http://localhost:11434

models:
  stt:
    local/whisper-turbo:
      provider: whisper
      model: turbo
  llm:
    ollama/llama3.2:3b:
      provider: ollama
      model: llama3.2:3b
  tts:
    local/kokoro:
      provider: kokoro
      model: kokoro
```

### 8. Deploy with Docker

```bash
docker compose up -d
```

See the [installation guide](/guide/installation) for Docker details.

## Keeping LiveKit for real-time transport

VoiceGateway replaces LiveKit's *inference routing* layer, not LiveKit's *real-time transport*. You still use LiveKit's rooms, tracks, and WebRTC infrastructure. VoiceGateway plugs into the `livekit-agents` framework as a provider source -- the agent session, room management, and media transport remain unchanged.

## Related pages

- [Quick Start](/guide/quick-start)
- [Migrating from LiteLLM](/migration/from-litellm)
- [Version Upgrades](/migration/version-upgrades)
- [Troubleshooting](/reference/troubleshooting)
