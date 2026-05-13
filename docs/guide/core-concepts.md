# Core Concepts

This page defines the key abstractions in VoiceGateway. Understanding these concepts will help you navigate the configuration and API.

## Inference module

The public Python surface. `voicegateway.inference` mirrors `livekit.agents.inference` so an agent written for LiveKit Cloud Inference moves to VoiceGateway with one import-line change. Each factory call (`STT`, `LLM`, `TTS`) constructs the matching LiveKit plugin and wraps it with VG's middleware.

```python
from voicegateway import inference
stt = inference.STT("deepgram/nova-3")
```

See: [Quick Start](/guide/quick-start), [First Agent](/guide/first-agent), [Python SDK Reference](/api/python-sdk)

## Provider

A backend service that performs inference. VoiceGateway supports 11 providers: 7 cloud (Deepgram, OpenAI, Anthropic, Groq, Cartesia, ElevenLabs, AssemblyAI) and 4 local (Whisper, Ollama, Kokoro, Piper). Each provider wraps a corresponding `livekit.plugins.<name>` package and is instantiated lazily on first inference call.

See: [Providers](/configuration/providers)

## Model ID

A string in `"provider/model"` format that uniquely identifies a model. For example, `deepgram/nova-3`, `openai/gpt-4.1-mini`, or `cartesia/sonic-3`. STT model IDs can include a language suffix (`deepgram/nova-3:en`), and TTS model IDs can include a voice suffix (`cartesia/sonic-3:narrator`). LLM model IDs preserve trailing colons verbatim, so Ollama tags like `ollama/qwen2.5:3b` work as expected.

See: [Models](/configuration/models)

## Modality

The type of inference operation: **STT** (speech-to-text), **LLM** (large language model), or **TTS** (text-to-speech). Each provider supports one or more modalities. The factory classes `inference.STT`, `inference.LLM`, and `inference.TTS` correspond directly to these three modalities.

See: [Providers](/configuration/providers) for a modality support matrix

## Project

A logical grouping for per-project provider keys, cost tracking, and budget enforcement. Each project has a name, optional `daily_budget`, a `budget_action` (warn, throttle, or block), and an optional `providers:` block carrying its own API keys. The active project is set via `inference.set_project(...)`, the `VOICEGW_ACTIVE_PROJECT` env var, the `default_project` field in YAML, or the auto-created `default` fallback.

See: [Projects](/configuration/projects)

## Session

A logical voice conversation: one caller turn through STT, LLM, and TTS. VoiceGateway tags every request from the same async context with a shared `session_id` (`"vg-<uuid>"`), accumulating cost and modality data into the `sessions` table.

See: [Python SDK Reference](/api/python-sdk#session-correlation)

## Fallback Chain

An ordered list of model IDs in `voicegw.yaml` for resolver-time fallback. Walk the chain at agent startup using the inference factories; the first model whose provider plugin imports cleanly and whose key resolves wins. Once `AgentSession` starts, that model is used for the whole call. v0.0.6 will add a first-class `fallback=` parameter to the inference factories.

See: [voicegw.yaml Reference](/configuration/voicegw-yaml)

## Budget Action

The enforcement behavior when a project exceeds its `daily_budget`. Three options:

- **warn** -- log a warning but allow requests to continue.
- **throttle** -- add artificial delay to requests to slow down consumption.
- **block** -- reject requests entirely until the budget resets.

See: [Projects](/configuration/projects)

## Middleware

Processing layers that wrap every inference call. VoiceGateway includes four built-in middleware components: cost tracking, latency monitoring, rate limiting, and request logging. Middleware runs transparently around each provider invocation. You control which middleware is active via the `observability` config section.

See: [Observability](/configuration/observability)

## Config Layer

VoiceGateway manages configuration from two sources: the `voicegw.yaml` file and a SQLite database (for models and projects created at runtime via the dashboard or MCP). At startup, the `ConfigManager` merges both sources. Changes made through the API or MCP are persisted to SQLite and merged on next `refresh_config()`.

See: [voicegw.yaml Reference](/configuration/voicegw-yaml), [Environment Variables](/configuration/environment-variables)
