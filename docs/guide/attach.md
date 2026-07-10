---
title: attach() (observability)
description: attach() is the single passive meter for cost and latency. One call binds VoiceGateway to a LiveKit AgentSession or a Pipecat PipelineTask.
---

# attach() (observability)

`attach()` is VoiceGateway's **observe** seam: a single, passive meter for cost
and latency. You call it once, hand it your agent session or pipeline task, and
every STT, LLM, and TTS call flowing through that session is priced and recorded.
It never reroutes, throttles, or blocks a call; measuring and controlling are a
hard line, and control lives in [`guard()`](/guide/guard).

Because `attach()` is the *single* meter, pairing it with `guard()` never
double-counts. `guard()` writes no metrics of its own.

<Note>
For the conceptual model behind these two seams (attach observes, guard controls,
RequestRecord, Sink, projects, voice-prices), read [Core Concepts](/guide/core-concepts) first.
</Note>

## Signature

```python
voicegateway.attach(
    target,                      # LiveKit AgentSession OR Pipecat PipelineTask
    *,
    project: str = "default",
    agent_id: str | None = None,     # fleet label; defaults to VOICEGW_AGENT_ID or hostname
    tenant_id: str | None = None,    # optional per-call tenant attribution
    channel: str | None = None,      # "telephony" | "web"; auto-detected when omitted
    collector_url: str | None = None,  # fleet push target (env: VOICEGW_COLLECTOR_URL)
    api_key: str | None = None,        # collector key (env: VOICEGW_API_KEY)
    sink: Sink | None = None,          # advanced/testing override
) -> str                             # the correlation session id stamped on every row
```

`attach()` detects the target's framework by type and installs the matching
observer. The return value is the session id that ties every captured row
together, so you can echo it into your own logs.

## Wiring

<Tabs>
  <Tab title="LiveKit">
    Construct your `AgentSession` with native `livekit.plugins` providers, then
    attach before you start it:

    ```python
    from livekit.agents import Agent, AgentSession
    from livekit.plugins import deepgram, openai, cartesia

    import voicegateway


    async def entrypoint(ctx):
        await ctx.connect()

        session = AgentSession(
            stt=deepgram.STT(model="nova-3"),
            llm=openai.LLM(model="gpt-4o-mini"),
            tts=cartesia.TTS(model="sonic-3"),
        )

        # One call. Every STT / LLM / TTS metric is metered from here on.
        voicegateway.attach(session, project="my-agent")

        await session.start(agent=Agent(instructions="Be helpful."), room=ctx.room)
    ```

    On the LiveKit path `attach()` subscribes to the per-component
    `metrics_collected` events, so it works with any plugin without wrapping it.
    The session's `close` event finalizes the meter (drains in-flight writes and
    flushes the sink), so a graceful shutdown loses nothing.
  </Tab>
  <Tab title="Pipecat">
    Pipecat has no cumulative usage aggregate, so `attach()` sums the metrics it
    observes. Enable Pipecat's metrics on the task, then either call `attach(task)`
    or pass an exported `Observer` to the task constructor. Both do the same thing.

    <Warning>
    Pipecat metrics must be explicitly enabled. Without `enable_metrics` and
    `enable_usage_metrics`, Pipecat emits no usage frames and there is nothing for
    `attach()` to record.
    </Warning>

    **Option A: attach(task).**

    ```python
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.task import PipelineParams, PipelineTask
    from pipecat.services.deepgram.stt import DeepgramSTTService
    from pipecat.services.openai.llm import OpenAILLMService
    from pipecat.services.cartesia.tts import CartesiaTTSService

    import voicegateway

    stt = DeepgramSTTService(api_key=DEEPGRAM_API_KEY)
    llm = OpenAILLMService(api_key=OPENAI_API_KEY, model="gpt-4o-mini")
    tts = CartesiaTTSService(api_key=CARTESIA_API_KEY, voice_id=VOICE_ID)

    pipeline = Pipeline([transport.input(), stt, llm, tts, transport.output()])
    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
    )

    voicegateway.attach(task, project="my-agent")
    ```

    **Option B: Observer in the constructor.**

    ```python
    import voicegateway

    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[voicegateway.Observer(project="my-agent")],
    )
    ```

    `voicegateway.Observer` takes the same keyword arguments as `attach()`
    (`project`, `agent_id`, `tenant_id`, `channel`, `collector_url`, `api_key`,
    `sink`). The observer finalizes itself on the pipeline end (`EndFrame`): it
    drains any pending STT audio into a final record and flushes the sink.
  </Tab>
</Tabs>

## What it records

`attach()` writes one row per request through a `Sink` (local SQLite by default,
or a remote collector when `collector_url` / `VOICEGW_COLLECTOR_URL` is set).
Each row carries:

| Field group | What it captures |
|---|---|
| modality + provider + model | `stt` / `llm` / `tts`, the provider name, and the model id |
| usage units | STT audio minutes, LLM prompt / completion / cached tokens, TTS characters |
| cost | priced through `voice-prices` from the usage units |
| latency | time to first byte (`ttfb_ms`) and total latency |
| correlation | the session id, `project`, `agent_id`, `tenant_id`, and `channel` |
| routing | `fallback_from` and `status` when a `guard()` fell back to this provider |

### How each modality is measured

- **LLM**: from the usage metric (`prompt_tokens`, `completion_tokens`,
  `cache_read_input_tokens` on LiveKit or Pipecat's `LLMTokenUsage`).
- **TTS**: from the character count in the usage metric.
- **STT**: derived from audio duration as the baseline. On Pipecat the observer
  accumulates the `AudioRawFrame` bytes routed to each STT service and converts
  bytes to seconds (16-bit mono PCM). A service's direct usage is used when it
  exposes one.

## Channel and session

**Channel** is `"telephony"` or `"web"`. When you omit `channel=`, `attach()`
auto-detects it from the transport: a LiveKit SIP remote participant means a
phone call (else web); a Pipecat Twilio / Telnyx / Plivo (etc.) serializer means
telephony, while a Daily / WebRTC / websocket transport means web. Pass
`channel=` explicitly to override the guess.

**Session** correlation is automatic. Every row from one attached session (or
pipeline) shares the returned session id, which the dashboard uses to group a
conversation and its per-turn timeline. On LiveKit the id is created when the
session context opens; on Pipecat it is created when you attach.

<Tip>
Multi-tenant operators pass `tenant_id=` to slice costs per customer. Each row
is labelled so the dashboard and API can filter or group by tenant. See
[Multi-tenant quickstart](/guide/multi-tenant-quickstart) for a worked example.
</Tip>

## See also

- [Core Concepts](/guide/core-concepts): the two-seam model, RequestRecord, Sink, projects, and voice-prices.
- [guard()](/guide/guard): the active control seam that composes with `attach()`.
- [Frameworks and extras](/guide/frameworks): install `voicegateway[livekit]` vs
  `voicegateway[pipecat]`.
- [Migration guide](/guide/migration-attach-guard): moving off the deprecated
  `voicegateway.LLM/STT/TTS` factories to native + attach + guard.
