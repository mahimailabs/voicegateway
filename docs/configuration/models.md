---
title: Models
description: How VoiceGateway identifies every model with a provider/model string, including language and voice suffixes, custom alias registration, and example model IDs for STT, LLM, and TTS.
---

# Models

## Model ID format

Every model in VoiceGateway is identified by a `provider/model` string:

```
deepgram/nova-3
openai/gpt-4.1-mini
cartesia/sonic-3
```

### Language suffixes (STT)

STT model IDs accept an optional language code after a colon:

```
deepgram/nova-3:en
deepgram/nova-3:es
```

### Voice suffixes (TTS)

TTS model IDs accept an optional voice ID after a colon:

```
cartesia/sonic-3:narrator-male
openai/tts-1:nova
```

### Ollama tags (LLM)

LLM model IDs preserve trailing colons verbatim, so Ollama version tags pass through intact:

```
ollama/qwen2.5:3b
ollama/llama3.2:3b
```

<Note>
STT and TTS strip the last colon segment at parse time. LLM does not. This asymmetry mirrors how LiveKit agents handle the model string.
</Note>

---

## Registering custom model aliases

Register aliases under `models` in `voicegw.yaml`. Aliases surface in the dashboard and CLI. They group models by modality with an optional `default_voice` for TTS entries.

```yaml
models:
  stt:
    fast-stt:
      provider: deepgram
      model: nova-3
    accurate-stt:
      provider: assemblyai
      model: universal-2
  llm:
    reasoning:
      provider: anthropic
      model: claude-sonnet-4-5
    fast-chat:
      provider: groq
      model: llama-3.1-8b-instant
  tts:
    narrator:
      provider: cartesia
      model: sonic-3
      default_voice: narrator-male
    cheap-tts:
      provider: piper
      model: en_US-lessac-medium
```

Each entry supports:

| Field | Required | Description |
|---|---|---|
| `provider` | yes | Provider identifier (e.g. `deepgram`, `anthropic`) |
| `model` | yes | Model name at the provider |
| `default_voice` | no | Default voice for TTS model aliases |

### Via the dashboard

Models can also be registered through the web dashboard at the daemon URL (default `http://localhost:8080`). Dashboard-registered models are persisted in SQLite and merged with YAML config at startup.

### Via MCP

If the MCP server is running (`voicegw mcp`), you can register models through MCP tool calls from your IDE.

---

## Model reference

### STT models

| Model ID | Provider | Notes |
|---|---|---|
| `deepgram/nova-3` | Deepgram | Best cloud STT accuracy |
| `deepgram/nova-2` | Deepgram | Lower cost alternative |
| `openai/whisper-1` | OpenAI | OpenAI-hosted Whisper |
| `groq/whisper-large-v3` | Groq | Fast Whisper via Groq |
| `assemblyai/universal-2` | AssemblyAI | High accuracy, single tier |
| `local/whisper-large-v3` | Whisper (local) | Best local STT |
| `local/whisper-base` | Whisper (local) | Fastest local STT |

### LLM models

| Model ID | Provider | Notes |
|---|---|---|
| `openai/gpt-4.1-mini` | OpenAI | Good cost/quality balance |
| `openai/gpt-4.1` | OpenAI | Best quality |
| `anthropic/claude-sonnet-4-20250514` | Anthropic | Strong reasoning |
| `anthropic/claude-haiku-4-5` | Anthropic | Fast and cheap |
| `groq/llama-3.3-70b-versatile` | Groq | Fast open-source LLM |
| `groq/llama-3.1-8b-instant` | Groq | Ultra-fast, smaller model |
| `ollama/llama3.2:3b` | Ollama (local) | Local LLM via Ollama |
| `ollama/mistral:7b` | Ollama (local) | Local Mistral |

### TTS models

| Model ID | Provider | Notes |
|---|---|---|
| `cartesia/sonic-3` | Cartesia | Low-latency streaming |
| `openai/tts-1` | OpenAI | Fast cloud TTS |
| `openai/tts-1-hd` | OpenAI | High quality cloud TTS |
| `elevenlabs/eleven_multilingual_v2` | ElevenLabs | 29 languages |
| `elevenlabs/eleven_turbo_v2` | ElevenLabs | Faster, English-focused |
| `deepgram/aura-asteria-en` | Deepgram | Deepgram TTS |
| `local/kokoro` | Kokoro (local) | Lightweight local TTS |
| `local/piper:en_US-lessac-medium` | Piper (local) | Fast offline TTS; voice ID after `:` |

---

See [Providers](/configuration/providers) for which providers support each modality.
See [Stacks](/configuration/stacks) for bundling model IDs into named tiers.
See [voicegw.yaml reference](/configuration/voicegw-yaml) for the full config file shape.
