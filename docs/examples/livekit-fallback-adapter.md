# LiveKit FallbackAdapter Integration

This page shows how to compose VoiceGateway's `inference` factories with LiveKit's `FallbackAdapter` to get runtime, error-driven failover during an active call. VoiceGateway's own resolver-time fallback (see [Fallback Chains](/examples/fallback-chains)) handles startup selection; the LiveKit Agents framework supplies the runtime piece.

## Why LiveKit FallbackAdapter, not VG's own

LiveKit Agents ships `stt.FallbackAdapter`, `llm.FallbackAdapter`, and `tts.FallbackAdapter` as part of the framework. Three reasons VoiceGateway does not duplicate this:

1. The functionality already exists in the same framework your agent already depends on.
2. The LiveKit team maintains and tests it alongside the rest of the agents framework.
3. The adapter integrates with `AgentSession`'s `ErrorEvent` flow, which is the canonical way to surface a chain-exhausted state to the agent.

The composition pattern below is the recommended way to deliver "primary provider down, fall back to a backup, keep the call alive" for a VG-routed agent.

## The composition

```python
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli, llm, stt, tts
from livekit.plugins import silero
from voicegateway import inference


async def entrypoint(ctx: JobContext):
    await ctx.connect()

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=stt.FallbackAdapter([
            inference.STT("deepgram/nova-3"),         # primary
            inference.STT("groq/whisper-large-v3"),   # secondary cloud
            inference.STT("local/whisper-large-v3"),  # local fallback
        ]),
        llm=llm.FallbackAdapter([
            inference.LLM("openai/gpt-4.1-mini"),
            inference.LLM("anthropic/claude-sonnet-4-20250514"),
            inference.LLM("ollama/qwen2.5:3b"),
        ]),
        tts=tts.FallbackAdapter([
            inference.TTS("cartesia/sonic-3"),
            inference.TTS("elevenlabs/eleven_turbo_v2_5"),
            inference.TTS("local/kokoro"),
        ]),
    )

    await session.start(
        agent=Agent(instructions="You are a helpful voice assistant."),
        room=ctx.room,
    )


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
```

Each `inference.STT / LLM / TTS` call returns a native LiveKit plugin instance wrapped by VoiceGateway's instrumentation. `FallbackAdapter` accepts those instances directly: no extra adapter layer, no plugin shim. The active project resolves the same way it does for any other inference call (`set_project`, env var, or `default_project` in YAML).

## What triggers fallback

`FallbackAdapter` is runtime and error-driven. Per [LiveKit's reference](https://docs.livekit.io/reference/agents/events/):

- A failed request (network error, provider 5xx, authentication failure mid-call) is automatically resubmitted to the next provider in the chain.
- The failed provider is marked unhealthy, and the adapter stops routing new requests to it.
- The adapter periodically rechecks the unhealthy provider in the background.
- When the primary recovers, traffic shifts back to it.

This is the runtime piece VoiceGateway intentionally does not implement.

## Recommended chain patterns

For voice agents specifically:

- **Cloud-to-cloud-to-local.** Primary cloud (best quality, lowest latency), secondary cloud (different provider, similar quality), local model (worst-case fallback that works offline).
- **Match modality strengths.** STT chains should put the lowest-latency provider first (Deepgram Nova for English-heavy voice agents). TTS chains should also put the lowest-latency provider first (Cartesia Sonic). LLM chains can prioritize quality over latency since reasoning latency is a smaller share of total turn time.
- **Anchor with a local model.** Avoid all-cloud chains where a regional outage could take down every provider in the list. A single local fallback at the end of the chain gives you true outage coverage at the cost of degraded quality during the outage.

## How VoiceGateway's cost tracking interacts

Each attempt VoiceGateway sees is logged as a separate `RequestRecord` in SQLite. If `FallbackAdapter` calls the primary and that call fails:

- The primary attempt is logged with `status = "error"` and the captured `error_message`.
- When `FallbackAdapter` retries with the secondary, that call is logged separately with `status = "success"` (or another `error` if the secondary also fails).

You can correlate the two records by timestamp clustering and project tag. The dashboard's request log view shows the status next to each row.

The `fallback_from` field on `RequestRecord` is reserved for the v0.0.6 resolver-time fallback parameter on the inference factories, not for LiveKit's runtime `FallbackAdapter`: VG sees each attempt as an independent provider call. To trace runtime fallback events today, filter the request log by project and look for adjacent records with `status = "error"` followed by `status = "success"`.

For project budget enforcement, every attempt counts against the project budget independently. A primary that fails and a secondary that succeeds will both be billed (the primary because the provider counts the failed request, the secondary because it served the actual response). This matches what your provider invoices will show.

## Error handling

When every provider in the chain fails for a single request, `AgentSession` emits an `ErrorEvent` with `error.recoverable = False`. Handle it via the standard event subscription:

```python
@session.on("error")
def on_error(event):
    if not event.error.recoverable:
        # Every provider in the chain failed for this request.
        # Inform the user, log to your incident pipeline, page on-call, etc.
        ...
```

If `event.error.recoverable` is `True`, the chain advanced to the next provider successfully and the session continues. The event is informational; you can log it for later analysis but no intervention is required.

## When this is not what you need

- **You only want startup-time provider selection.** Use the manual chain walk pattern in [Fallback Chains](/examples/fallback-chains). v0.0.6 will replace it with a built-in `fallback=` parameter on the inference factories.
- **You only have one cloud provider configured.** `FallbackAdapter` is overkill. A single-provider config plus a circuit breaker outside the agent is simpler.
- **You are on Node.js.** `stt.FallbackAdapter` is Python-only. `llm.FallbackAdapter` and `tts.FallbackAdapter` are available on Node.js per the [LiveKit reference](https://docs.livekit.io/reference/agents/events/); for STT failover on Node.js you need a different approach.

## Related

- [Fallback Chains](/examples/fallback-chains): VoiceGateway's resolver-time fallback. Complementary to `FallbackAdapter`. Resolver-time picks the first available provider at agent startup; runtime fallback handles failures during a call.
- [Quick Start](/guide/quick-start)
- [LiveKit Agents events reference](https://docs.livekit.io/reference/agents/events/)
