---
title: What is VoiceGateway?
description: VoiceGateway is a thin observability and control layer for LiveKit and Pipecat voice agents. It tracks per-modality cost across STT, LLM, and TTS, and adds fallback and budget enforcement without a proxy hop.
---

VoiceGateway slots between your agent framework and your provider SDKs. It does not sit in the audio or data path. Instead, it hooks two seams: `attach()` is a passive observer that meters every STT, LLM, and TTS call; `guard()` is an active control wrapper that adds fallback chains, rate limits, and spend caps around any provider.

## The problem

Production voice AI agents juggle three provider categories at once: STT (Deepgram, AssemblyAI, Whisper), LLM (OpenAI, Anthropic, Groq, Ollama), and TTS (Cartesia, ElevenLabs, Kokoro, Piper). Each category has its own pricing unit: STT is billed in audio-minutes, LLM in tokens, and TTS in characters. No single dashboard shows you what a call cost across all three.

As a project grows, the pain compounds:

- **No per-call cost visibility.** You see monthly totals per provider, never the cost of one conversation.
- **No fallback story.** When a provider goes down at 2 AM, your agent goes silent.
- **Per-project budgets are impossible.** When multiple agents or customers share the same API keys, there is no easy way to track or cap spend per project.
- **Local and cloud code paths diverge.** Running Whisper locally for development and Deepgram in production means two different wiring setups.

## The two-seam model

VoiceGateway exposes exactly two integration points.

| Seam | Role | What it does |
|---|---|---|
| `attach(session)` | observe (passive) | Meters every provider call; records cost, latency, and tokens to storage |
| `guard(provider)` | control (active) | Wraps one provider; adds fallback, rate limiting, and budget caps |

`attach()` is the **only** source of metrics. `guard()` writes no metrics of its own. Use both together and nothing is double-counted.

<Tabs>
  <Tab title="LiveKit">
    ```python
    from livekit.agents import AgentSession
    from livekit.plugins import deepgram, openai, cartesia
    from voicegateway import attach, guard

    session = AgentSession(
        stt=guard(deepgram.STT(), fallback=[openai.STT()]),
        llm=guard(openai.LLM("gpt-4o"), budget="$5.00/day"),
        tts=cartesia.TTS("sonic-3"),
    )
    attach(session, project="my-project")
    await session.start(agent=MyAgent(), room=ctx.room)
    ```
  </Tab>
  <Tab title="Pipecat">
    ```python
    from pipecat.pipeline.task import PipelineTask
    from pipecat.services.deepgram import DeepgramSTTService
    from pipecat.services.openai import OpenAILLMService
    from pipecat.services.cartesia import CartesiaTTSService
    from voicegateway import attach, guard

    task = PipelineTask(pipeline=Pipeline([
        guard(DeepgramSTTService(), fallback=[openai_stt]),
        guard(OpenAILLMService(model="gpt-4o"), budget="$5.00/day"),
        CartesiaTTSService(voice_id="..."),
    ]))
    attach(task, project="my-project")
    await task.run()
    ```
  </Tab>
</Tabs>

## Modality-aware cost tracking

Voice calls mix three different pricing units. VoiceGateway tracks each one separately and converts them to a dollar cost per call:

| Modality | Unit | Example |
|---|---|---|
| STT | audio-minutes | Deepgram: $0.0059/min |
| LLM | input + output tokens | OpenAI gpt-4o: $2.50/$10.00 per 1M |
| TTS | characters | Cartesia sonic-3: $65 per 1M chars |

Prices are maintained in [`voice-prices`](https://github.com/mahimailabs/voice-prices), a fork of `pydantic/genai-prices` extended for audio modalities. The `voicegw reconcile` command verifies VoiceGateway's calculated totals against your actual provider invoice.

Local models (Whisper, Kokoro, Piper, Ollama) are recorded as zero-cost, so per-project totals always reflect what you actually pay.

## Where it fits in your stack

```
Your agent code (LiveKit or Pipecat)
  ├── attach(session / task)        # passive observer, meters every call
  └── guard(provider)               # active wrapper, adds control per provider
       ├── native provider SDK      # deepgram, openai, cartesia, etc.
       └── fallback providers       # tried in order when primary fails

Records flow to:
  └── storage (SQLite or Cloud ClickHouse)
       ├── voicegw dashboard        # per-call cost, latency, provider breakdown
       ├── voicegw reconcile        # verify against provider invoices
       └── MCP server               # query from your AI editor
```

VoiceGateway does not sit in the audio or inference path. There is no proxy hop and no added latency on happy-path calls.

## Supported providers

**Cloud:**

| Provider | STT | LLM | TTS |
|---|---|---|---|
| Deepgram | Yes | | Yes |
| OpenAI | Yes | Yes | Yes |
| Anthropic | | Yes | |
| Groq | Yes | Yes | |
| Cartesia | | | Yes |
| ElevenLabs | | | Yes |
| AssemblyAI | Yes | | |

**Local:**

| Provider | STT | LLM | TTS |
|---|---|---|---|
| Whisper (faster-whisper) | Yes | | |
| Ollama | | Yes | |
| Kokoro | | | Yes |
| Piper | | | Yes |

## Self-host or Hosted Cloud

VoiceGateway ships as an open-source Python package you run yourself (SQLite, single process). VoiceGateway Hosted Cloud is a managed version with a ClickHouse-backed ingest endpoint, multi-tenant isolation, and a shared dashboard at `dash.voicegateway.dev`. The two seams (`attach` / `guard`) work identically in both; only the storage sink differs.

## Next steps

<CardGroup cols={2}>
  <Card title="Quick start" icon="bolt" href="/guide/quick-start">
    Get running in 5 minutes against a Deepgram + OpenAI + Cartesia stack.
  </Card>
  <Card title="attach()" icon="eye" href="/guide/attach">
    Full reference for the passive observability seam.
  </Card>
  <Card title="guard()" icon="shield" href="/guide/guard">
    Add fallback, rate limiting, and budget caps to any provider.
  </Card>
  <Card title="Which path?" icon="map" href="/guide/decision-tree">
    Self-host or Cloud? attach-only or guard too? A decision guide.
  </Card>
</CardGroup>
