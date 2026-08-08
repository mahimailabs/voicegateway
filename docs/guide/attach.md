---
title: attach() (observe)
description: attach() is VoiceGateway's single passive meter for cost and latency. One call binds it to a LiveKit AgentSession or a Pipecat PipelineTask.
---
`attach()` is VoiceGateway's **observe** seam: a passive meter for cost and
latency. Call it once with your `AgentSession` or `PipelineTask` and every
STT, LLM, and TTS call flowing through it is priced and recorded. It never
reroutes, throttles, or blocks a call: control lives in [`guard()`](/guide/guard).

Because `attach()` is the *only* source of metrics, pairing it with `guard()`
never double-counts; `guard()` writes no metrics of its own.

## Signature

```python
voicegateway.attach(
    session,                          # LiveKit AgentSession OR Pipecat PipelineTask
    *,
    project: str | None = None,       # env: VOICEGW_PROJECT, else "default"
    agent_id: str | None = None,      # env: VOICEGW_AGENT_ID, else hostname
    tenant_id: str | None = None,     # optional per-call tenant attribution
    channel: str | None = None,       # "telephony" | "web"; auto-detected
    collector_url: str | None = None, # fleet push target (env: VOICEGW_COLLECTOR_URL)
    api_key: str | None = None,       # collector key (env: VOICEGW_API_KEY)
    sink: Sink | None = None,         # advanced/testing override
    room: str | None = None,          # LiveKit room name for probe correlation; auto-resolved
    heartbeat: bool = False,          # register + heartbeat this process in the fleet roster
    transcript: bool = True,          # capture the call transcript (LiveKit only for now)
    snapshots: bool = False,          # capture conversation-state snapshots for replay (opt-in)
) -> str                              # correlation session id stamped on every row
```

<Warning>
`attach()` picks its framework by inspecting the session's type. An object it
does not recognize as a LiveKit or Pipecat target falls through the LiveKit
path silently: no error, no rows written. Pass a real `AgentSession` or
`PipelineTask`.
</Warning>

## Wiring

<Tabs>
  <Tab title="LiveKit">
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

        voicegateway.attach(session, project="my-agent")

        await session.start(agent=Agent(instructions="Be helpful."), room=ctx.room)
    ```

    `attach()` subscribes to the per-component `metrics_collected` events
    (works with any plugin, no wrapping) and finalizes on the session's
    `close` event: in-flight writes drain, then the sink flushes.
  </Tab>
  <Tab title="Pipecat">
    Pipecat has no cumulative usage aggregate, so `attach()` sums the metrics
    it observes. `enable_metrics` and `enable_usage_metrics` must both be on,
    or Pipecat emits no usage frames and there is nothing to record:

    ```python
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.task import PipelineParams, PipelineTask
    from pipecat.services.deepgram.stt import DeepgramSTTService
    from pipecat.services.openai.llm import OpenAILLMService
    from pipecat.services.cartesia.tts import CartesiaTTSService

    import voicegateway

    transport = ...  # your Pipecat transport: DailyTransport, a SIP serializer, LocalAudioTransport, etc.

    pipeline = Pipeline([
        transport.input(),
        DeepgramSTTService(api_key=DEEPGRAM_API_KEY),
        OpenAILLMService(api_key=OPENAI_API_KEY, model="gpt-4o-mini"),
        CartesiaTTSService(api_key=CARTESIA_API_KEY, voice_id=VOICE_ID),
        transport.output(),
    ])
    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
    )

    voicegateway.attach(task, project="my-agent")
    ```

    `voicegateway.Observer(...)` takes the same core keyword arguments and
    can be passed to `PipelineTask(observers=[...])` instead, as an
    equivalent to `attach(task)`. It finalizes on the pipeline's `EndFrame`.
  </Tab>
</Tabs>

## What it records

One row per request, through a `Sink` (local SQLite by default, or a
collector when `collector_url` / `VOICEGW_COLLECTOR_URL` is set):

| Field group | What it captures |
|---|---|
| modality + provider + model | `stt` / `llm` / `tts`, provider name, model id |
| usage units | STT audio minutes, LLM prompt/completion/cached tokens, TTS characters |
| cost | priced through `voice-prices` from the usage units |
| latency | time to first byte (`ttfb_ms`) and total latency |
| correlation | session id, `project`, `agent_id`, `tenant_id`, `channel` |
| routing | `fallback_from` / `status` when a `guard()` fell back to this provider |

LLM and TTS usage come straight from the framework's usage metric. STT is
derived from audio duration: on Pipecat, the observer accumulates each STT
service's `AudioRawFrame` bytes and converts 16-bit mono PCM to seconds.

## Channel, session, and tenant

**Channel** (`"telephony"` / `"web"`) auto-detects from the transport when
omitted: a LiveKit SIP participant or a Pipecat Twilio/Telnyx/Plivo
serializer means telephony; anything else means web.

**Session** correlation is automatic and identical on both frameworks: the
id is created (or reused) via Python `contextvars` the moment `attach()`
runs. Every row from that session shares it, and a task spawned inside the
same async context inherits it too, with no argument needed.

**Tenant** attribution is opt-in: pass `tenant_id=` when one deployment
serves several customers. See [Tenant attribution](/guide/multi-tenant-quickstart).

## room, heartbeat, transcript, snapshots (LiveKit)

| Param | Behavior |
|---|---|
| `room` | LiveKit room name stamped on each row, so `voicegw livekit latency` can read the STT/LLM/TTS split back by room. Auto-resolved from the running job context. |
| `heartbeat=True` | Registers this process in the fleet roster and heartbeats its presence (the dashboard's Fleet/Agents view). Best for single-process agents where `attach()` is the sole writer. In LiveKit's per-call subprocess model (`agent dev`), call `register_worker(agent_id, local=True)` at your `__main__` boot instead, and skip `heartbeat=True` there, because the subprocess would become a second writer of the same roster row. |
| `transcript=True` (default) | On close, user/agent turns are read from the framework's conversation history and written to local storage for the Calls page. `transcript=False` disables it per attach; `VOICEGW_TRANSCRIPTS=0` kills it fleet-wide (the env var wins over the argument). Pipecat accepts the flag but does not capture transcripts yet. |
| `snapshots=True` | Captures conversation-state snapshots for [session replay](/cli/replay). **Off by default**, the one place this differs from `transcript`. `VOICEGW_SNAPSHOTS=0` kills it fleet-wide and beats the argument. Pipecat accepts the flag and does not capture yet. |

<Warning>
`snapshots` defaults off because it is a strictly larger disclosure than a transcript. A
transcript is what the caller said. A snapshot carries your **system prompt**, the full
message history, and every tool call's arguments and result, so it captures your own
prompt and whatever payloads your tools handle. That should be asked for, not assumed.
</Warning>

Snapshots need a local sink. When `VOICEGW_COLLECTOR_URL` is set, capture is skipped:
a collector has no replay tables, and the dashboard reads replay from the local store, so
capturing there would buffer rows nothing could flush.

## Config: what actually gates writes

`attach()` always writes cost and latency; no config toggle turns it off. It
resolves storage from environment variables only, never from `voicegw.yaml`:
`VOICEGW_DB_PATH` for local SQLite (default
`~/.config/voicegateway/voicegw.db`), or `VOICEGW_COLLECTOR_URL` +
`VOICEGW_API_KEY` for fleet mode.

`voicegw.yaml`'s `observability:` block (`latency_tracking`, `cost_tracking`,
`request_logging`, all default `true`) configures the Gateway behind
`voicegw serve` / `voicegw status` / the dashboard: a separate reader-side
process, not `attach()`'s own write path.

## See also

- [guard()](/guide/guard): the active control seam that composes with `attach()`.
- [Frameworks and extras](/guide/frameworks): install `voicegateway[livekit]` vs
  `voicegateway[pipecat]`.
