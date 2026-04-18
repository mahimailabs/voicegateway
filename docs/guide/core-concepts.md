# Core Concepts

This page defines the key abstractions in VoiceGateway. Understanding these concepts will help you navigate the configuration and API.

## Gateway

The central orchestrator. The `Gateway` class is the single entry point for all VoiceGateway operations. It loads your `voicegw.yaml` config, initializes the router and middleware pipeline, and exposes methods like `stt()`, `llm()`, `tts()`, and `stack()`. You typically create one `Gateway` instance per application.

```python
from voicegateway import Gateway
gw = Gateway()  # loads voicegw.yaml from default locations
```

See: [Quick Start](/guide/quick-start), [First Agent](/guide/first-agent)

## Provider

A backend service that performs inference. VoiceGateway supports 11 providers: 7 cloud (Deepgram, OpenAI, Anthropic, Groq, Cartesia, ElevenLabs, AssemblyAI) and 4 local (Whisper, Ollama, Kokoro, Piper). Each provider implements the `BaseProvider` interface and is instantiated lazily on first use.

See: [Providers](/configuration/providers)

## Model ID

A string in `"provider/model"` format that uniquely identifies a model. For example, `deepgram/nova-3`, `openai/gpt-4.1-mini`, or `cartesia/sonic-3`. STT model IDs can include a language suffix (`deepgram/nova-3:en`), and TTS model IDs can include a voice suffix (`cartesia/sonic-3:narrator`). The `ModelId` class parses these strings.

See: [Models](/configuration/models)

## Modality

The type of inference operation: **STT** (speech-to-text), **LLM** (large language model), or **TTS** (text-to-speech). Each provider supports one or more modalities. The gateway methods `stt()`, `llm()`, and `tts()` correspond directly to these three modalities.

See: [Providers](/configuration/providers) for a modality support matrix

## Stack

A named bundle of models covering all three modalities. Stacks let you define preset combinations like `premium` (cloud providers, best quality), `budget` (cheaper models), or `local` (fully offline). You resolve a stack to get an `(stt, llm, tts)` tuple in one call.

```python
stt, llm, tts = gw.stack("premium")
```

See: [Stacks](/configuration/stacks)

## Project

A logical grouping for cost tracking and budget enforcement. Each project has a name, optional `daily_budget`, a `budget_action` (warn, throttle, or block), and an optional `default_stack`. When you pass a `project=` argument to gateway methods, costs are attributed to that project.

See: [Projects](/configuration/projects)

## Fallback Chain

An ordered list of model IDs for a given modality. When the primary provider fails or is unavailable, VoiceGateway automatically tries the next provider in the chain. Fallback chains are defined per-modality in `voicegw.yaml` and accessed via `stt_with_fallback()`, `llm_with_fallback()`, and `tts_with_fallback()`.

See: [voicegw.yaml Reference](/configuration/voicegw-yaml)

## Budget Action

The enforcement behavior when a project exceeds its `daily_budget`. Three options:

- **warn** -- log a warning but allow requests to continue.
- **throttle** -- add artificial delay to requests to slow down consumption.
- **block** -- reject requests entirely until the budget resets.

See: [Projects](/configuration/projects)

## Middleware

Processing layers that wrap every provider call. VoiceGateway includes five built-in middleware components: cost tracking, latency monitoring, rate limiting, request logging, and fallback chains. Middleware runs transparently around each provider invocation. You control which middleware is active via the `observability` config section.

See: [Observability](/configuration/observability)

## Config Layer

VoiceGateway manages configuration from two sources: the `voicegw.yaml` file and a SQLite database (for models and projects created at runtime via the dashboard or MCP). At startup, the `ConfigManager` merges both sources. Changes made through the API or MCP are persisted to SQLite and merged on next `refresh_config()`.

See: [voicegw.yaml Reference](/configuration/voicegw-yaml), [Environment Variables](/configuration/environment-variables)
