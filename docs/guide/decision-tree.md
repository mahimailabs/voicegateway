# Is VoiceGateway right for you?

A short matrix to help you pick the right tool for your workload before you invest time integrating.

## The short answer

| If you are... | Use |
|---|---|
| Building a LiveKit voice agent and want per-modality cost tracking | **VoiceGateway** |
| Self-hosting voice with local + cloud model unification (Whisper + Ollama + Kokoro/Piper alongside Deepgram, OpenAI, etc.) | **VoiceGateway** |
| Building a text-only LLM app (chatbot, RAG, code generation) | [LiteLLM](https://docs.litellm.ai/) |
| Wanting a hosted multi-tenant LLM proxy with no infrastructure to run | [OpenRouter](https://openrouter.ai/) |
| At production scale on Cloudflare and want a gateway in that stack | [Cloudflare AI Gateway](https://developers.cloudflare.com/ai-gateway/) |
| Building on managed LiveKit Cloud and happy with bundled inference pricing | LiveKit Inference (built into [LiveKit Cloud](https://livekit.io/cloud)) |

If your case is not in the matrix, the longer-form notes below cover the remaining common ones.

## Detailed breakdown

### You are building a LiveKit voice agent

VoiceGateway is purpose-built for this. `voicegateway.inference.STT/LLM/TTS` mirror `livekit.agents.inference` signature for signature, so swapping one import line in an existing LiveKit Cloud Inference agent is enough. The factories return wrapped LiveKit plugin instances that drop straight into `AgentSession(stt=, llm=, tts=)` with no proxy hop or plugin shim. Cost tracking happens transparently per modality (audio-minutes for STT, tokens for LLM, characters for TTS) against the active project. `voicegw reconcile` verifies the calculated cost against your actual provider invoice during the first month.

If you use LiveKit Cloud's hosted Inference and are happy with the bundled inference pricing, you do not need VoiceGateway. Pick VG when you want your own provider keys, full per-call cost visibility, and control over the model catalog without leaving the LiveKit Agents code shape.

### You are building a text-only LLM app

[LiteLLM](https://docs.litellm.ai/) is the right tool. 100+ LLM providers, an OpenAI-compatible HTTP proxy that any existing OpenAI client can hit unchanged, multi-level budgets (per-key, per-team, per-user, per-model, per-agent), and a mature admin UI. VoiceGateway has 4 LLM provider integrations and no OpenAI-compat HTTP shim, so for general-purpose text LLM workloads it is the wrong fit.

LiteLLM also ships `/v1/audio/transcriptions` and `/v1/audio/speech` for non-real-time audio (batch transcription, async TTS rendering). Pick those when your audio workload is request/response, not a real-time voice conversation.

### You want a hosted multi-tenant proxy with no ops

[OpenRouter](https://openrouter.ai/) is the fit. They run the proxy, handle billing, and aggregate access to a wide LLM catalog through a single API key. You run no infrastructure. Trade-off: you pay an OpenRouter markup on top of provider prices, and the path is managed-only. You cannot self-host OpenRouter.

### You are at scale on Cloudflare

[Cloudflare AI Gateway](https://developers.cloudflare.com/ai-gateway/) integrates with the rest of the Cloudflare stack (Workers, R2, KV, Durable Objects, Logpush). If your existing voice or LLM workload already lives there, the gateway is a natural extension. Trade-off: closed source, managed, and LLM-only routing today.

### You are self-hosting voice with local and cloud models

VoiceGateway's local model support (Whisper via `faster-whisper`, Kokoro for TTS, Piper for TTS, Ollama for LLM) lives in the same `inference` factory as the cloud providers. Switching `inference.STT("deepgram/nova-3")` for `inference.STT("local/whisper-large-v3")` is a single string change. Cost tracking knows local models are zero-cost. No separate orchestrator, no second tool. This is the configuration where VoiceGateway is most distinctive: no other gateway in the matrix unifies cloud and local providers under one factory with cost-aware routing.

## What VoiceGateway is not

A few cases where VoiceGateway is the wrong tool, called out so you do not discover them after integrating:

- **Not an OpenAI-compatible HTTP proxy.** There is no `/v1/chat/completions` endpoint, no `/v1/audio/transcriptions` endpoint, no HTTP shim of any kind for inference. The `voicegateway.inference` Python module is the integration surface. If your callers expect to make OpenAI-shaped HTTP requests, use LiteLLM.
- **Not a horizontally scaled multi-tenant gateway.** SQLite is the storage layer; the supported topology is single-instance. Multiple instances pointing at the same SQLite file each have an independent in-memory budget cache, so per-project budgets are not strictly enforced across replicas. This is fine for self-hosted single-team deployments and a poor fit for SaaS infrastructure that needs Postgres + horizontal scale.
- **Not a real-time fallback engine.** VoiceGateway's resolver-time fallback walks a chain at startup, not during a call. For runtime / error-driven failover during an active call (the "Cloud outage? Switch to local" pattern), compose LiveKit's `FallbackAdapter` around VG `inference.*` instances.
- **Not a key-rotation system.** API keys can be re-entered if the encryption secret changes, but there is no built-in rotation tooling, MultiFernet versioning, or KMS integration. Treat the encryption secret as a long-lived credential.

## Still unsure?

Jump to the [quick start](/guide/quick-start) and try VoiceGateway in five minutes against a Deepgram + OpenAI + Cartesia stack. The shape fits your workload or it does not; the integration is small enough that finding out is cheap.
