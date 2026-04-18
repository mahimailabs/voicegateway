# What is VoiceGateway?

VoiceGateway is a **self-hosted inference gateway** purpose-built for voice AI applications. It sits between your application code and the speech/language model providers you depend on, giving you a single interface to route STT (speech-to-text), LLM (large language model), and TTS (text-to-speech) requests across cloud services and local models.

## The problem

Building a production voice AI agent means juggling multiple providers. You need Deepgram or AssemblyAI for transcription, OpenAI or Anthropic for reasoning, and Cartesia or ElevenLabs for speech synthesis. Each provider has its own SDK, authentication scheme, pricing model, and failure modes.

As your project grows, so do the operational headaches:

- **Vendor lock-in** -- switching from one STT provider to another means rewriting integration code.
- **No unified cost tracking** -- you have to log into each provider's dashboard separately to understand spend.
- **No fallback story** -- if your primary TTS provider goes down at 2 AM, your agent goes silent.
- **Per-project budgets are impossible** -- when multiple teams or customers share the same API keys, there is no easy way to track or cap usage per project.
- **Local/cloud split** -- running Whisper locally for development but Deepgram in production requires maintaining two code paths.

## The solution

VoiceGateway solves these problems with a thin routing layer that normalizes provider interfaces behind a consistent API. You describe your providers, models, and policies in a single YAML file (`voicegw.yaml`), then call `gw.stt()`, `gw.llm()`, and `gw.tts()` from your Python code. VoiceGateway handles the rest: provider instantiation, middleware execution (cost tracking, latency monitoring, rate limiting), fallback chains, and budget enforcement.

```python
from voicegateway import Gateway

gw = Gateway()

stt = gw.stt("deepgram/nova-3")
llm = gw.llm("anthropic/claude-sonnet-4-20250514")
tts = gw.tts("cartesia/sonic-3")
```

Switching providers is a one-line config change. Adding fallback chains is a few lines of YAML. Per-project budgets are built in.

## Who is it for?

- **Voice AI engineers** building agents with [LiveKit Agents](https://docs.livekit.io/agents/) or similar frameworks who want clean provider abstraction.
- **Platform teams** running multi-tenant voice infrastructure that need per-project cost tracking and budget controls.
- **Indie developers** who want to use local models (Whisper, Kokoro, Piper) during development and cloud providers in production, without changing application code.
- **Cost-conscious teams** who need visibility into per-request costs across STT, LLM, and TTS with a single dashboard.

## Feature comparison

| Feature | VoiceGateway | Direct SDK calls | LiteLLM |
|---|---|---|---|
| STT + LLM + TTS routing | Yes | Manual | LLM only |
| Unified config (YAML) | Yes | No | Partial |
| Fallback chains | Yes | Manual | Yes |
| Per-project cost tracking | Yes | No | No |
| Budget enforcement (warn/throttle/block) | Yes | No | No |
| Local model support | Yes (Whisper, Kokoro, Piper, Ollama) | N/A | Ollama only |
| Named stacks (premium/budget/local) | Yes | No | No |
| Web dashboard | Yes | No | No |
| MCP server integration | Yes | No | No |
| LiveKit Agents compatible | Yes | Yes | Partial |

## Supported providers

VoiceGateway ships with 11 provider integrations spanning cloud and local:

**Cloud providers:**

| Provider | STT | LLM | TTS |
|---|---|---|---|
| Deepgram | Yes | -- | Yes |
| OpenAI | Yes | Yes | Yes |
| Anthropic | -- | Yes | -- |
| Groq | Yes | Yes | -- |
| Cartesia | -- | -- | Yes |
| ElevenLabs | -- | -- | Yes |
| AssemblyAI | Yes | -- | -- |

**Local providers:**

| Provider | STT | LLM | TTS |
|---|---|---|---|
| Whisper | Yes | -- | -- |
| Ollama | -- | Yes | -- |
| Kokoro | -- | -- | Yes |
| Piper | -- | -- | Yes |

## Architecture overview

The request flow through VoiceGateway follows a clean pipeline:

```
Your code
  --> Gateway.stt() / llm() / tts()
    --> Router (resolves "provider/model" string)
      --> Provider instance
        --> Middleware pipeline
            - Cost tracking
            - Latency monitoring
            - Rate limiting
            - Fallback chains
            - Budget enforcement
        --> SQLite storage
          --> Dashboard (reads stored data)
```

Key architectural decisions:

- **Async throughout** -- all database, HTTP, and provider operations use `async/await`.
- **Lazy provider instantiation** -- providers are created on first use via a registry factory, so unused providers cost nothing.
- **Modular installs** -- `pip install voicegateway[openai,deepgram]` installs only the SDKs you need.
- **Pydantic validation** -- the config schema uses `extra="forbid"` to catch typos in your YAML before they cause runtime errors.
- **SQLite storage** -- request logs, cost records, and project data are stored locally in a SQLite database. No external dependencies.

For a deeper dive into the internal architecture, see the [Architecture](/architecture/) section.

## Next steps

- [Quick Start](/guide/quick-start) -- get running in 5 minutes
- [Installation](/guide/installation) -- system requirements and install options
- [First Agent](/guide/first-agent) -- build a working voice agent with LiveKit
- [Core Concepts](/guide/core-concepts) -- understand the key abstractions
