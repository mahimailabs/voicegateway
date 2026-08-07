---
title: Providers
description: All 11 providers VoiceGateway supports, with modality coverage, recommended models, per-provider config blocks, and project-level key overrides.
---
VoiceGateway supports 11 providers across cloud and local deployments. Each provider extends the `BaseProvider` interface and is instantiated lazily on first use.

## Common fields

Every provider block supports these fields:

| Field | Type | Default | Description |
|---|---|---|---|
| `api_key` | string | `""` | API key, typically via `${ENV_VAR}` substitution |
| `base_url` | string | provider default | Override the default API endpoint |
| `enabled` | bool | `true` | Disable a provider without removing its config |

---

## Cloud providers

### Deepgram

Modalities: STT, TTS. Required: `api_key`.

Recommended models: `deepgram/nova-3` (best accuracy), `deepgram/nova-2` (lower cost), `deepgram/aura-asteria-en` (TTS).

```yaml
providers:
  deepgram:
    api_key: ${DEEPGRAM_API_KEY}
```

### OpenAI

Modalities: STT, LLM, TTS. Required: `api_key`.

Recommended models: `openai/whisper-1` (STT), `openai/gpt-4.1-mini` (LLM, balanced), `openai/gpt-4.1` (LLM, best quality), `openai/tts-1` (TTS, fast), `openai/tts-1-hd` (TTS, high quality).

```yaml
providers:
  openai:
    api_key: ${OPENAI_API_KEY}
```

### Anthropic

Modalities: LLM. Required: `api_key`.

Recommended models: `anthropic/claude-sonnet-4-5` (balanced), `anthropic/claude-opus-4-1` (highest quality).

```yaml
providers:
  anthropic:
    api_key: ${ANTHROPIC_API_KEY}
```

### Groq

Modalities: STT, LLM. Required: `api_key`.

Recommended models: `groq/whisper-large-v3` (STT), `groq/llama-3.3-70b-versatile`, `groq/llama-3.1-8b-instant` (LLM).

```yaml
providers:
  groq:
    api_key: ${GROQ_API_KEY}
```

### Cartesia

Modalities: TTS. Required: `api_key`.

Recommended models: `cartesia/sonic-3` (latest, best quality, low latency).

```yaml
providers:
  cartesia:
    api_key: ${CARTESIA_API_KEY}
```

### ElevenLabs

Modalities: TTS. Required: `api_key`.

Recommended models: `elevenlabs/eleven_multilingual_v2` (29 languages), `elevenlabs/eleven_turbo_v2_5`.

```yaml
providers:
  elevenlabs:
    api_key: ${ELEVENLABS_API_KEY}
```

### AssemblyAI

Modalities: STT. Required: `api_key`.

Recommended models: `assemblyai/universal-2` (single-tier, streaming and batch).

```yaml
providers:
  assemblyai:
    api_key: ${ASSEMBLYAI_API_KEY}
```

---

## Local providers

Local providers run on your own hardware with no API keys required. They suit development, privacy-sensitive deployments, and offline operation.

### Whisper

Modalities: STT. No API key required; model weights download on first use.

Recommended models: `local/whisper-large-v3` (best accuracy), `local/whisper-base` (fastest). Requires a capable CPU or GPU.

```yaml
providers:
  whisper:
    enabled: true
```

### Ollama

Modalities: LLM. Required: a running Ollama server.

Recommended models: `ollama/llama3.2:3b`, `ollama/mistral:7b`, `ollama/phi3:mini`. Use `docker compose --profile local up -d` to start Ollama alongside VoiceGateway.

```yaml
providers:
  ollama:
    base_url: http://localhost:11434
```

### Kokoro

Modalities: TTS. No API key required.

Recommended models: `local/kokoro`. Lightweight local TTS, good for development and testing.

```yaml
providers:
  kokoro:
    enabled: true
```

### Piper

Modalities: TTS. No API key required; ONNX voice models download on first use.

Recommended models: `local/piper:en_US-lessac-medium`, `local/piper:en_US-amy-low`. The voice ID follows the colon.

```yaml
providers:
  piper:
    enabled: true
```

---

## Provider modality matrix

| Provider | STT | LLM | TTS | Type |
|---|---|---|---|---|
| Deepgram | Yes | -- | Yes | Cloud |
| OpenAI | Yes | Yes | Yes | Cloud |
| Anthropic | -- | Yes | -- | Cloud |
| Groq | Yes | Yes | -- | Cloud |
| Cartesia | -- | -- | Yes | Cloud |
| ElevenLabs | -- | -- | Yes | Cloud |
| AssemblyAI | Yes | -- | -- | Cloud |
| Whisper | Yes | -- | -- | Local |
| Ollama | -- | Yes | -- | Local |
| Kokoro | -- | -- | Yes | Local |
| Piper | -- | -- | Yes | Local |

---

## Per-project provider keys

The top-level `providers` block sets the default keys. Each project can override the keys it uses with its own `providers` block:

```yaml
providers:
  openai:
    api_key: ${DEFAULT_OPENAI_KEY}

projects:
  tonys-pizza:
    name: Tony's Pizza
    providers:
      openai:
        api_key: ${TONYS_OPENAI_KEY}
```

The router picks the right key automatically based on the active project. See [Projects](/configuration/projects) for how the active project is resolved.

---

## DB-managed providers

Beyond YAML, providers can be added at runtime via the MCP server or the dashboard. These rows live in the `managed_providers` table with their API keys Fernet-encrypted by `VOICEGW_SECRET`. The resolution order is: YAML providers (top-level + per-project) first, then DB-managed providers for any missing entries.

---

See [voicegw.yaml reference](/configuration/voicegw-yaml) for the full config file shape.
See [Models](/configuration/models) for the `provider/model` string format.
