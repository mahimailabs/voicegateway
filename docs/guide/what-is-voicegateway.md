---
title: What is VoiceGateway?
description: VoiceGateway is a profiler for voice agents and the infrastructure they run on. It meters per-modality cost across STT, LLM, and TTS without a proxy hop, and adds fallback and budget enforcement where you ask for it.
---

VoiceGateway slots beside your agent framework, not between you and it. It hooks two
seams: `attach()` is a passive observer that meters every STT, LLM, and TTS call;
`guard()` is an active control wrapper that adds fallback chains, rate limits, and
spend caps around a provider you choose.

Metering the agent is where most people start. It is not the whole product. See
[What you can profile](/guide/what-you-can-profile) for the SFU and SIP layers.

## The problem

A production voice agent juggles three provider categories at once: STT (Deepgram,
AssemblyAI, Whisper), LLM (OpenAI, Anthropic, Groq, Ollama), and TTS (Cartesia,
ElevenLabs, Kokoro, Piper). Each bills in a different unit. STT is audio-minutes,
LLM is tokens, TTS is characters. No provider dashboard shows you what one
conversation cost across all three.

As a project grows the pain compounds:

- **No per-call cost visibility.** You see monthly totals per provider, never the cost of one conversation.
- **No fallback story.** When a provider goes down at 2 AM, your agent goes silent.
- **Per-project budgets are impossible.** When several agents or customers share the same API keys, there is no way to cap spend per project.
- **Local and cloud paths diverge.** Whisper in development and Deepgram in production means two wiring setups.

## The two-seam model

VoiceGateway exposes exactly two integration points.

| Seam | Role | What it does |
|---|---|---|
| `attach(session)` | observe (passive) | Meters every provider call; records cost, latency, and usage to storage |
| `guard(provider)` | control (active) | Wraps one provider; adds fallback, rate limiting, and budget caps |

`attach()` is the only source of metrics. `guard()` writes none of its own, so using
both together never double-counts.

<Tabs>
  <Tab title="LiveKit">
    ```python
    from livekit.agents import Agent, AgentSession
    from livekit.plugins import cartesia, deepgram, openai

    import voicegateway

    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        llm=voicegateway.guard(
            openai.LLM(model="gpt-4o-mini"),
            fallback=[openai.LLM(model="gpt-4o")],
            budget="$5.00/day",
        ),
        tts=cartesia.TTS(model="sonic-3"),
    )
    voicegateway.attach(session, project="my-agent")
    await session.start(agent=Agent(instructions="Be helpful."), room=ctx.room)
    ```
  </Tab>
  <Tab title="Pipecat">
    ```python
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.task import PipelineParams, PipelineTask
    from pipecat.services.cartesia.tts import CartesiaTTSService
    from pipecat.services.deepgram.stt import DeepgramSTTService
    from pipecat.services.openai.llm import OpenAILLMService

    import voicegateway

    pipeline = Pipeline([
        DeepgramSTTService(api_key=DEEPGRAM_API_KEY),
        voicegateway.guard(
            OpenAILLMService(api_key=OPENAI_API_KEY, model="gpt-4o-mini"),
            budget="$5.00/day",
        ),
        CartesiaTTSService(api_key=CARTESIA_API_KEY, voice_id=VOICE_ID),
    ])
    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
    )
    voicegateway.attach(task, project="my-agent")
    ```
  </Tab>
</Tabs>

## Modality-aware cost tracking

Voice calls mix three pricing units. VoiceGateway tracks each separately and
converts them to a dollar cost per call:

| Modality | Billing unit | What VoiceGateway records |
|---|---|---|
| STT | audio-minutes | duration of the audio sent to the provider |
| LLM | input and output tokens | prompt, completion, and cached tokens |
| TTS | characters | characters sent to synthesis |

Rates come from [`voice-prices`](https://github.com/mahimailabs/voice-prices), a fork of
`pydantic/genai-prices` extended for audio modalities. `voicegw reconcile` verifies the
calculated totals against your provider invoice.

### What gets priced

There is no list of supported providers. `attach()` meters whatever your framework
emits, and the model id decides what happens next:

| Model id | Cost | Pricing source |
|---|---|---|
| starts with `local/` or `ollama/` | always `0` | `voicegateway-local` |
| known to `voice-prices` | the catalog rate | `voice-prices@<version>` |
| anything else | none, the row is metered but unpriced | empty |

The Costs page shows that source per row, so you can always tell which case you landed
in. The distinction between the first and third rows is deliberate: free because you
host it yourself is not the same as unpriced because nobody recognised the model.

<Warning>
Self-hosted models are matched on the `local/` and `ollama/` prefixes, not by name. An
agent reporting `whisper-large-v3` is unpriced; the same model reported as
`local/whisper-large-v3` prices at zero.
</Warning>

## Where it fits

```
Your agent code (LiveKit or Pipecat)
  ├── attach(session / task)        # passive observer, meters every call
  └── guard(provider)               # active wrapper, adds control per provider
       ├── native provider SDK      # deepgram, openai, cartesia, etc.
       └── fallback providers       # tried in order when primary fails

Records flow to:
  └── storage (SQLite, or Cloud ClickHouse)
       ├── voicegw serve            # the daemon: HTTP API and the dashboard
       ├── voicegw logs / costs     # per-request rows and totals in the terminal
       ├── voicegw reconcile        # verify against provider invoices
       └── MCP server               # query from your AI editor
```

VoiceGateway does not sit in the audio or inference path. There is no proxy hop and
no added latency on happy-path calls.

## When something else is the better fit

Most tools VoiceGateway gets compared to are LLM proxies. They sit in the request path and
route text completions, which is a different problem.

| If you are... | Use |
|---|---|
| Building a LiveKit or Pipecat voice agent and want per-modality cost | VoiceGateway |
| Profiling a LiveKit SFU or SIP path you operate | VoiceGateway |
| Building a text-only LLM app | [LiteLLM](https://docs.litellm.ai/) |
| Wanting a hosted multi-tenant LLM proxy with no infrastructure | [OpenRouter](https://openrouter.ai/) |
| At scale on Cloudflare and wanting a gateway in that stack | [Cloudflare AI Gateway](https://developers.cloudflare.com/ai-gateway/) |
| On LiveKit Cloud and happy with bundled inference pricing | LiveKit Inference |

Next: [what you can profile](/guide/what-you-can-profile), or go straight to the
[quickstart](/get-started).
