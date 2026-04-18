# Migrating from LiteLLM

LiteLLM is an excellent LLM proxy that normalizes 100+ LLM providers behind an OpenAI-compatible API. If your workload is text-only (chatbots, RAG, code generation), LiteLLM may be all you need.

VoiceGateway starts where LiteLLM stops: it routes **STT, LLM, and TTS** through a single gateway, adds per-project cost tracking across all three modalities, and exposes an MCP server so coding agents can manage the gateway conversationally.

## Feature comparison

| Capability | LiteLLM | VoiceGateway |
|---|---|---|
| LLM routing | 100+ providers | 5 providers (OpenAI, Anthropic, Groq, Ollama, plus any OpenAI-compatible) |
| STT routing | -- | Deepgram, OpenAI Whisper, AssemblyAI, Groq Whisper, local Whisper |
| TTS routing | -- | Cartesia, ElevenLabs, Deepgram Aura, OpenAI TTS, Kokoro, Piper |
| Cost tracking | Per-key, per-model | Per-project, per-modality, daily budgets with warn/block actions |
| Budget enforcement | Soft limits | Hard block or warn per project, checked before every request |
| Fallback chains | Model-level | Per-modality fallback chains (STT, LLM, TTS independently) |
| Local models | Via Ollama | Ollama + native Whisper, Kokoro, Piper (no network hop) |
| Dashboard | Admin UI | Neo-Brutalism React dashboard with cost/latency charts |
| MCP server | -- | 17 tools for managing the gateway from Claude Code, Cursor, Codex |
| LiveKit integration | -- | Built on `livekit-agents`, first-class plugin compatibility |
| Config format | YAML / env vars | YAML with `${ENV_VAR}` substitution |

## Side-by-side: LLM call

### LiteLLM

```python
import litellm

response = litellm.completion(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Hello"}],
)
```

### VoiceGateway

```python
from voicegateway import Gateway

gw = Gateway()  # reads voicegw.yaml
llm = gw.llm("openai/gpt-4o-mini", project="my-app")
# Returns a LiveKit LLM plugin instance, ready for agent pipelines
```

The key difference: VoiceGateway returns **LiveKit plugin instances**, not raw API responses. This makes it a drop-in for LiveKit agent pipelines.

## Side-by-side: adding STT and TTS

### LiteLLM (manual wiring)

```python
import litellm
from deepgram import DeepgramClient  # separate SDK
from cartesia import Cartesia        # separate SDK

# You manage three SDKs, three API keys, three cost calculations
dg = DeepgramClient(os.environ["DEEPGRAM_API_KEY"])
llm_response = litellm.completion(model="gpt-4o-mini", messages=msgs)
cartesia = Cartesia(api_key=os.environ["CARTESIA_API_KEY"])
```

### VoiceGateway (unified)

```python
from voicegateway import Gateway

gw = Gateway()  # one config, one cost tracker

stt = gw.stt("deepgram/nova-3", project="my-app")
llm = gw.llm("openai/gpt-4o-mini", project="my-app")
tts = gw.tts("cartesia/sonic-3", project="my-app")

# All three share the same project budget, same dashboard, same logs
```

## Migration steps

### 1. Install VoiceGateway

```bash
pip install voicegateway[cloud,dashboard]
```

### 2. Create your config

```bash
voicegw init
```

Edit `voicegw.yaml` to add your existing API keys (same keys you use with LiteLLM):

```yaml
providers:
  openai:
    api_key: ${OPENAI_API_KEY}
  anthropic:
    api_key: ${ANTHROPIC_API_KEY}
  deepgram:
    api_key: ${DEEPGRAM_API_KEY}

models:
  llm:
    openai/gpt-4o-mini:
      provider: openai
      model: gpt-4o-mini
    anthropic/claude-3.5-sonnet:
      provider: anthropic
      model: claude-3.5-sonnet
  stt:
    deepgram/nova-3:
      provider: deepgram
      model: nova-3
  tts:
    cartesia/sonic-3:
      provider: cartesia
      model: sonic-3
```

### 3. Replace LiteLLM calls

Find every `litellm.completion()` call and replace it with `gw.llm()`. If you have STT or TTS code using separate SDKs, consolidate those too.

### 4. Add project budgets

```yaml
projects:
  my-app:
    name: My Application
    daily_budget: 25.00
    budget_action: warn  # or "block"
```

### 5. Start the dashboard

```bash
voicegw dashboard
# Open http://localhost:9090
```

You now have unified cost visibility across STT, LLM, and TTS -- something LiteLLM cannot provide.

### 6. (Optional) Enable the MCP server

```bash
voicegw mcp --transport stdio
```

Add to your Claude Code config to manage the gateway from your terminal. See the [MCP documentation](/mcp/) for setup details.

## When to stay with LiteLLM

- You only need LLM routing (no voice workloads)
- You need 100+ LLM provider support
- You are using LiteLLM's OpenAI-compatible proxy endpoint for existing tooling
- You do not use LiveKit

## When to switch to VoiceGateway

- You have STT and/or TTS workloads alongside LLM
- You want per-project cost tracking across all modalities
- You are building LiveKit-based voice agents
- You want budget enforcement that can block requests before they hit the API
- You want a single dashboard for all voice AI costs

## Related pages

- [Quick Start](/guide/quick-start)
- [Migrating from LiveKit Inference](/migration/from-livekit-inference)
- [Version Upgrades](/migration/version-upgrades)
- [FAQ](/reference/faq)
