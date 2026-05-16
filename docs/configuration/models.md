# Models

## Model ID format

Every model in VoiceGateway is identified by a string in `provider/model` format.

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

LLM model IDs preserve trailing colons verbatim, so Ollama tags survive:

```
ollama/qwen2.5:3b
ollama/llama3.2:3b
```

This asymmetry mirrors `livekit.agents.inference`: STT and TTS strip the last colon segment, LLM does not.

### Using model IDs in code

```python
from voicegateway import inference

# Pass model ID strings directly to inference factories.
stt = inference.STT("deepgram/nova-3:en")          # :en parsed as language
llm = inference.LLM("openai/gpt-4.1-mini")
tts = inference.TTS("cartesia/sonic-3:narrator-male")  # :voice-id parsed as voice
llm_local = inference.LLM("ollama/qwen2.5:3b")     # :3b kept as part of model name
```

## Registering custom models

You can register model aliases in `voicegw.yaml` under the `models` section. The aliases surface in the dashboard and CLI for display purposes; the `voicegateway.inference` module parses `provider/model` strings directly from the factory call, so an alias does not change runtime behaviour. Aliases are organised by modality (stt, llm, tts).

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

Each model entry supports:

- `provider` (string, required) -- the provider identifier
- `model` (string) -- the model name at the provider
- `default_voice` (string, optional) -- default voice for TTS models

### Via the dashboard

Models can also be registered through the web dashboard at the daemon URL (default `http://localhost:8080`). Models added through the dashboard are persisted in the SQLite database and merged with the YAML config at startup.

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
| `local/piper:en_US-lessac-medium` | Piper (local) | Fast offline TTS (voice ID after `:`) |

See: [Providers](/configuration/providers), [Stacks](/configuration/stacks), [voicegw.yaml Reference](/configuration/voicegw-yaml)
