# Providers

VoiceGateway supports 11 providers across cloud and local deployments. Each provider extends the `BaseProvider` interface and is instantiated lazily on first use.

## Cloud Providers

### Deepgram

- **Modalities:** STT, TTS
- **Required config:** `api_key`
- **Recommended models:**
  - STT: `deepgram/nova-3` (best accuracy), `deepgram/nova-2` (lower cost)
  - TTS: `deepgram/aura-asteria-en`
- **Pricing notes:** Pay-per-second for STT, pay-per-character for TTS. Nova-3 is priced higher than Nova-2 but offers better accuracy.

```yaml
providers:
  deepgram:
    api_key: ${DEEPGRAM_API_KEY}
```

### OpenAI

- **Modalities:** STT, LLM, TTS
- **Required config:** `api_key`
- **Recommended models:**
  - STT: `openai/whisper-1`
  - LLM: `openai/gpt-4.1-mini` (balanced), `openai/gpt-4.1` (best quality)
  - TTS: `openai/tts-1` (fast), `openai/tts-1-hd` (high quality)
- **Pricing notes:** Different pricing tiers per model. GPT-4.1-mini offers a good cost/quality balance for voice agents.

```yaml
providers:
  openai:
    api_key: ${OPENAI_API_KEY}
```

### Anthropic

- **Modalities:** LLM
- **Required config:** `api_key`
- **Recommended models:**
  - LLM: `anthropic/claude-3.5-sonnet` (balanced)
- **Pricing notes:** Per-token pricing. Check Anthropic's pricing page for latest rates.

```yaml
providers:
  anthropic:
    api_key: ${ANTHROPIC_API_KEY}
```

### Groq

- **Modalities:** STT, LLM
- **Required config:** `api_key`
- **Recommended models:**
  - STT: `groq/whisper-large-v3`
  - LLM: `groq/llama-3.3-70b-versatile`, `groq/llama-3.1-8b-instant`
- **Pricing notes:** Very fast inference at competitive pricing. The Whisper endpoint is significantly cheaper than OpenAI's hosted Whisper.

```yaml
providers:
  groq:
    api_key: ${GROQ_API_KEY}
```

### Cartesia

- **Modalities:** TTS
- **Required config:** `api_key`
- **Recommended models:**
  - TTS: `cartesia/sonic-3` (latest, best quality)
- **Pricing notes:** Pay-per-character. Known for low-latency streaming TTS.

```yaml
providers:
  cartesia:
    api_key: ${CARTESIA_API_KEY}
```

### ElevenLabs

- **Modalities:** TTS
- **Required config:** `api_key`
- **Recommended models:**
  - TTS: `elevenlabs/eleven_multilingual_v2`, `elevenlabs/eleven_turbo_v2`
- **Pricing notes:** Per-character pricing with monthly quotas depending on plan. Multilingual v2 supports 29 languages.

```yaml
providers:
  elevenlabs:
    api_key: ${ELEVENLABS_API_KEY}
```

### AssemblyAI

- **Modalities:** STT
- **Required config:** `api_key`
- **Recommended models:**
  - STT: `assemblyai/best` (highest accuracy), `assemblyai/nano` (fastest)
- **Pricing notes:** Per-second pricing. Offers real-time streaming and batch transcription.

```yaml
providers:
  assemblyai:
    api_key: ${ASSEMBLYAI_API_KEY}
```

---

## Local Providers

Local providers run on your own hardware with no API keys required. They are useful for development, privacy-sensitive deployments, and offline operation.

### Whisper

- **Modalities:** STT
- **Required config:** None (downloads model on first use)
- **Recommended models:**
  - STT: `whisper/large-v3` (best accuracy), `whisper/base` (fastest)
- **Notes:** Runs OpenAI Whisper locally via faster-whisper. Requires a capable CPU or GPU.

```yaml
providers:
  whisper:
    enabled: true
```

### Ollama

- **Modalities:** LLM
- **Required config:** `base_url` (defaults to `http://localhost:11434`)
- **Recommended models:**
  - LLM: `ollama/llama3`, `ollama/mistral`, `ollama/phi3`
- **Notes:** Requires a running Ollama server. Models are pulled on first use. Use `docker compose --profile local up -d` to start Ollama alongside VoiceGateway.

```yaml
providers:
  ollama:
    base_url: http://localhost:11434
```

### Kokoro

- **Modalities:** TTS
- **Required config:** None
- **Recommended models:**
  - TTS: `kokoro/default`
- **Notes:** Lightweight local TTS. Good for development and testing.

```yaml
providers:
  kokoro:
    enabled: true
```

### Piper

- **Modalities:** TTS
- **Required config:** None
- **Recommended models:**
  - TTS: `piper/en_US-lessac-medium`, `piper/en_US-amy-low`
- **Notes:** Fast offline TTS using ONNX models. Supports multiple languages and voices. Voice models are downloaded on first use.

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

## Common configuration options

All providers support these shared fields:

- `api_key` (string) -- API key, typically via `${ENV_VAR}` substitution
- `base_url` (string) -- override the default API endpoint
- `enabled` (bool, default `true`) -- disable a provider without removing its config

See: [voicegw.yaml Reference](/configuration/voicegw-yaml), [Models](/configuration/models)
