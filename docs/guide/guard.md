---
title: guard() (control)
description: guard() is the active control wrapper. It adds fallback, rate limiting, and spend caps around a native provider, on LiveKit and Pipecat. Control only, no metrics.
---
`guard()` is VoiceGateway's **control** seam. It wraps a native provider (a
LiveKit plugin or a Pipecat service) and returns a drop-in replacement of the
same type that adds three controls: fallback, rate limiting, and spend caps.

`guard()` writes **no** metrics; [`attach()`](/guide/attach) is the sole
meter, so `guard(provider)` plus `attach(session)` never double-counts. The
two seams never call each other: they coordinate only through ContextVars and
shared spend/limit state in the framework-neutral core. Use `attach()` on
every session; reach for `guard()` on the specific providers where you want
fallback or limits.

## Signature

```python
voicegateway.guard(
    provider,                    # a native LiveKit plugin OR Pipecat service
    *,
    fallback: list = (),         # same-framework providers, tried in order on error
    rate_limit: str | None = None,  # e.g. "60/min" or "5/s"
    budget: str | None = None,      # e.g. "$5.00/day" or "$100/month"
    project: str | None = None,     # project id for budget lookups; defaults to "default"
)                                # returns the SAME type it wrapped (drop-in)
```

Raises `ImportError` when the provider's framework extra is not installed,
and `ValueError` when `rate_limit` / `budget` can't be parsed or the
provider's framework isn't recognized (see [Frameworks and extras](/guide/frameworks)).

- **fallback**: on a primary-provider error, each fallback runs in order
  until one succeeds. `attach()` stamps `fallback_from=<primary>` and
  `status="fallback"` on the row for the provider that actually produced the
  result. If every provider fails, the last error is re-raised.
- **rate_limit**: a token bucket parsed from the DSL (`"60/min"`, `"5/s"`).
  An empty bucket raises `RateLimitExceeded`.
- **budget**: a spend cap parsed from the DSL (`"$5.00/day"`,
  `"$100/month"`). guard reads the window's accumulated spend from the core
  and raises `BudgetExceededError` when it is at or over the cap. This closes
  the measure-then-enforce loop: enforcement reads the cost data `attach()`
  already wrote.

## Fallback only covers pre-output failures

A real constraint on **both** frameworks, for any streaming modality (LLM,
TTS): guard opens the primary provider and starts consuming its stream. An
error before the first chunk moves to the next fallback; once the primary
has yielded a chunk, the request is committed to it and a later failure
surfaces rather than getting silently patched over. STT is a single
call-and-response on both frameworks, so it's naturally all-or-nothing. Size
a fallback chain for "primary is down" rather than "primary died mid-stream."

## Wiring

<Tabs>
  <Tab title="LiveKit">
    `guard()` returns a subclass of the LiveKit base (`livekit.agents.{llm,stt,tts}`)
    so the result slots into an `AgentSession` unchanged:

    ```python
    from livekit.agents import Agent, AgentSession
    from livekit.plugins import deepgram, openai, cartesia

    import voicegateway


    async def entrypoint(ctx):
        await ctx.connect()

        session = AgentSession(
            stt=deepgram.STT(model="nova-3"),
            llm=voicegateway.guard(
                openai.LLM(model="gpt-4o-mini"),
                fallback=[openai.LLM(model="gpt-4o")],
                rate_limit="60/min",
                budget="$5.00/day",
            ),
            tts=cartesia.TTS(model="sonic-3"),
        )

        voicegateway.attach(session, project="my-agent")  # the single meter
        await session.start(agent=Agent(instructions="Be helpful."), room=ctx.room)
    ```

    Guard any combination of modalities independently: only wrap the
    providers where you actually need fallback or limits.
  </Tab>
  <Tab title="Pipecat">
    The API is identical. Pass a native Pipecat service; `guard()` returns a
    wrapped service you place in the pipeline where the original went:

    ```python
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.task import PipelineParams, PipelineTask
    from pipecat.services.deepgram.stt import DeepgramSTTService
    from pipecat.services.openai.llm import OpenAILLMService
    from pipecat.services.cartesia.tts import CartesiaTTSService

    import voicegateway

    guarded_llm = voicegateway.guard(
        OpenAILLMService(api_key=OPENAI_API_KEY, model="gpt-4o-mini"),
        fallback=[OpenAILLMService(api_key=OPENAI_API_KEY, model="gpt-4o")],
        rate_limit="60/min",
        budget="$5.00/day",
    )

    pipeline = Pipeline([
        transport.input(),
        DeepgramSTTService(api_key=DEEPGRAM_API_KEY),
        guarded_llm,
        CartesiaTTSService(api_key=CARTESIA_API_KEY, voice_id=VOICE_ID),
        transport.output(),
    ])
    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
    )

    voicegateway.attach(task, project="my-agent")
    ```
  </Tab>
</Tabs>

## What guard() does not do

It does not manage provider keys (you pass already-configured, bring-your-
own-key providers), does not meter (cost and latency come from `attach()`),
and does not abstract across frameworks: a LiveKit guard takes LiveKit
fallbacks, a Pipecat guard takes Pipecat fallbacks.

## See also

- [attach()](/guide/attach): the passive meter guard composes with.
- [Frameworks and extras](/guide/frameworks): the framework-neutral core and
  the errors you get for a missing extra or an unrecognized provider.
