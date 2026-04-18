# Models

## Model ID format

Every model in VoiceGateway is identified by a string in `provider/model` format. The `ModelId` class parses these strings.

```
deepgram/nova-3
openai/gpt-4.1-mini
cartesia/sonic-3
```

### Language and voice suffixes

STT model IDs can include a language suffix separated by a colon:

```
deepgram/nova-3:en
deepgram/nova-3:es
```

TTS model IDs can include a voice suffix:

```
cartesia/sonic-3:narrator-male
openai/tts-1:nova
```

### Using model IDs in code

```python
from voicegateway import Gateway, ModelId

gw = Gateway()

# Pass model ID strings directly to gateway methods
stt = gw.stt("deepgram/nova-3")
llm = gw.llm("openai/gpt-4.1-mini")
tts = gw.tts("cartesia/sonic-3")

# Parse a model ID programmatically
mid = ModelId.parse("deepgram/nova-3")
print(mid.provider)  # "deepgram"
print(mid.model)     # "nova-3"
```

## Registering custom models

You can register model aliases in `voicegw.yaml` under the `models` section. Aliases are organized by modality (stt, llm, tts).

### Via YAML

```yaml
models:
  stt:
    fast-stt:
      provider: deepgram
      model: nova-3
    accurate-stt:
      provider: assemblyai
      model: best
  llm:
    reasoning:
      provider: anthropic
      model: claude-sonnet-4-20250514
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

Each model entry supports:

- `provider` (string, required) -- the provider identifier
- `model` (string) -- the model name at the provider
- `default_voice` (string, optional) -- default voice for TTS models

### Via the dashboard

Models can also be registered through the web dashboard at `http://localhost:9090`. Models added through the dashboard are persisted in the SQLite database and merged with the YAML config at startup.

### Via MCP

If you have the MCP server running (`voicegw mcp`), you can register models through MCP tool calls from your IDE. See the MCP documentation for details.

## Model examples

### STT models

| Model ID | Provider | Notes |
|---|---|---|
| `deepgram/nova-3` | Deepgram | Best cloud STT accuracy |
| `deepgram/nova-2` | Deepgram | Lower cost alternative |
| `openai/whisper-1` | OpenAI | OpenAI-hosted Whisper |
| `groq/whisper-large-v3` | Groq | Fast Whisper via Groq |
| `assemblyai/best` | AssemblyAI | Highest accuracy |
| `assemblyai/nano` | AssemblyAI | Fastest, lower accuracy |
| `whisper/large-v3` | Whisper (local) | Best local STT |
| `whisper/base` | Whisper (local) | Fastest local STT |

### LLM models

| Model ID | Provider | Notes |
|---|---|---|
| `openai/gpt-4.1-mini` | OpenAI | Good cost/quality balance |
| `openai/gpt-4.1` | OpenAI | Best quality |
| `anthropic/claude-sonnet-4-20250514` | Anthropic | Strong reasoning |
| `anthropic/claude-haiku-3-5` | Anthropic | Fast and cheap |
| `groq/llama-3.3-70b-versatile` | Groq | Fast open-source LLM |
| `groq/llama-3.1-8b-instant` | Groq | Ultra-fast, smaller model |
| `ollama/llama3` | Ollama (local) | Local LLM via Ollama |
| `ollama/mistral` | Ollama (local) | Local Mistral |

### TTS models

| Model ID | Provider | Notes |
|---|---|---|
| `cartesia/sonic-3` | Cartesia | Low-latency streaming |
| `openai/tts-1` | OpenAI | Fast cloud TTS |
| `openai/tts-1-hd` | OpenAI | High quality cloud TTS |
| `elevenlabs/eleven_multilingual_v2` | ElevenLabs | 29 languages |
| `elevenlabs/eleven_turbo_v2` | ElevenLabs | Faster, English-focused |
| `deepgram/aura-asteria-en` | Deepgram | Deepgram TTS |
| `kokoro/default` | Kokoro (local) | Lightweight local TTS |
| `piper/en_US-lessac-medium` | Piper (local) | Fast offline TTS |

See: [Providers](/configuration/providers), [Stacks](/configuration/stacks), [voicegw.yaml Reference](/configuration/voicegw-yaml)
