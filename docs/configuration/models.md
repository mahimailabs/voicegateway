---
title: Models and stacks
description: The provider/model id VoiceGateway uses to price every request, how to register named aliases and stack presets in voicegw.yaml, and how those bundles wire into attach().
---
## Model ID format

Every model is identified by a `provider/model` string:

```text
deepgram/nova-3
openai/gpt-4.1-mini
cartesia/sonic-3
```

This is the exact string cost tracking prices. When `attach()`/`guard()` wraps a plugin, VoiceGateway reads its `provider` and `model` attributes off the live instance (`f"{provider}/{model}"`) and passes that straight to `voice-prices`. Nothing is parsed or stripped from it.

<Warning>
Language and voice selection belong to your framework's native constructor kwargs (`language="en"` on a Deepgram STT plugin, `voice_id="..."` on a Cartesia TTS plugin), not to the model string. If a colon-suffixed value like `nova-3:en` ends up as the plugin's `model`, that suffix becomes part of the literal `model_id` VoiceGateway records, and the pricing lookup for that exact string fails for most cloud providers (cost records as unpriced). Local/self-hosted models (`local/*`, `ollama/*`) are unaffected: they price at $0 regardless of what follows the model name, which is why an Ollama tag (`ollama/llama3.2:3b`) or a Piper voice (`local/piper:en_US-lessac-medium`) is safe to write as-is.
</Warning>

---

## Registering custom model aliases

Register aliases under `models` in `voicegw.yaml`. Aliases surface in the dashboard and CLI as friendlier names; they group models by modality with an optional `default_voice` for TTS entries.

```yaml
models:
  stt:
    fast-stt:
      provider: deepgram
      model: nova-3
  llm:
    reasoning:
      provider: anthropic
      model: claude-sonnet-4-5
  tts:
    narrator:
      provider: cartesia
      model: sonic-3
      default_voice: narrator-male
```

| Field | Required | Description |
|---|---|---|
| `provider` | yes | Provider identifier (e.g. `deepgram`, `anthropic`) |
| `model` | yes | Model name at the provider |
| `default_voice` | no | Display-only default voice for TTS aliases |

Models can also be registered through the dashboard or, if `voicegw mcp` is running, through MCP tool calls from your IDE. Dashboard/MCP-registered models persist in SQLite and merge with YAML at startup (a YAML entry with the same id wins).

---

## Model reference

### STT

| Model ID | Provider | Notes |
|---|---|---|
| `deepgram/nova-3` | Deepgram | Best cloud STT accuracy |
| `deepgram/nova-2` | Deepgram | Lower cost alternative |
| `openai/whisper-1` | OpenAI | OpenAI-hosted Whisper |
| `groq/whisper-large-v3` | Groq | Fast Whisper via Groq |
| `assemblyai/universal-2` | AssemblyAI | High accuracy, single tier |
| `local/whisper-large-v3` | Whisper (local) | Best local STT; always $0 |

### LLM

| Model ID | Provider | Notes |
|---|---|---|
| `openai/gpt-4.1-mini` | OpenAI | Good cost/quality balance |
| `openai/gpt-4.1` | OpenAI | Best quality |
| `anthropic/claude-sonnet-4-5` | Anthropic | Strong reasoning |
| `anthropic/claude-haiku-4-5` | Anthropic | Fast and cheap |
| `groq/llama-3.3-70b-versatile` | Groq | Fast open-source LLM |
| `ollama/llama3.2:3b` | Ollama (local) | Always $0; tag after `:` passes through untouched |

### TTS

| Model ID | Provider | Notes |
|---|---|---|
| `cartesia/sonic-3` | Cartesia | Low-latency streaming |
| `openai/tts-1` | OpenAI | Fast cloud TTS |
| `openai/tts-1-hd` | OpenAI | High quality cloud TTS |
| `elevenlabs/eleven_multilingual_v2` | ElevenLabs | 29 languages |
| `deepgram/aura-asteria-en` | Deepgram | Deepgram TTS |
| `local/kokoro` | Kokoro (local) | Always $0 |
| `local/piper:en_US-lessac-medium` | Piper (local) | Always $0; voice id after `:` |

---

## Stacks

A stack is a named bundle mapping one name to an STT, LLM, and TTS model id: exactly those three keys, nothing else.

```yaml
stacks:
  premium:
    stt: deepgram/nova-3
    llm: anthropic/claude-sonnet-4-5
    tts: cartesia/sonic-3
  budget:
    stt: groq/whisper-large-v3
    llm: groq/llama-3.3-70b-versatile
    tts: local/piper:en_US-lessac-medium
```

Reference a stack from a project with `default_stack: premium` (see [Projects](/configuration/projects)).

<Note>
A stack is a dashboard and documentation hint only. Nothing reads `default_stack` at runtime to construct a provider. Pick the model ids from your chosen stack and pass them to your framework's native provider constructors yourself, then wrap with `attach()`/`guard()`:

```python
from livekit.agents import AgentSession
from livekit.plugins import deepgram, anthropic, cartesia
from voicegateway import attach

session = AgentSession(
    stt=deepgram.STT(model="nova-3"),
    llm=anthropic.LLM(model="claude-sonnet-4-5"),
    tts=cartesia.TTS(model="sonic-3"),
)
attach(session, project="acme")
```
</Note>

---

See [Projects](/configuration/projects) for `default_stack` and budget configuration.
See [attach()](/guide/attach) for wiring native providers so cost tracking sees them.
See [voicegw.yaml reference](/configuration/voicegw-yaml) for the full config file shape.
