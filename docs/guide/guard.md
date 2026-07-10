---
title: guard() (control)
description: guard() is the active control wrapper. It adds fallback, rate limiting, and spend caps around a native provider, on LiveKit and Pipecat. Control only, no metrics.
---

# guard() (control)

`guard()` is VoiceGateway's **control** seam. It wraps a native provider (a
LiveKit plugin or a Pipecat service) and returns a drop-in replacement of the
same type that adds three controls: fallback, rate limiting, and spend caps.

`guard()` is control only. It writes **no** metrics. [`attach()`](/guide/attach)
is the sole meter, so `guard(provider)` plus `attach(session)` never
double-counts. The two seams never call each other; they coordinate only through
the framework-neutral core (routing ContextVars and shared spend / limit state).

<Note>
For the conceptual model behind the two seams, see [Core Concepts](/guide/core-concepts).
For the observe side, see [attach()](/guide/attach).
</Note>

## Passive vs active

This is the core distinction. An observer can capture cost and latency but can
never reroute, throttle, or block a call, so control has to live in a wrapper
built at provider-construction time.

| | `attach()` | `guard()` |
|---|---|---|
| Role | observe (passive) | control (active) |
| Effect on the call | none; measures only | reroutes, throttles, or blocks |
| Metrics | the single meter | none (attach meters) |
| Where it sits | bound to the session / task | wraps one provider |
| Opt-in? | yes, per session | yes, per provider |

Use `attach()` on every session for cost and latency. Reach for `guard()` on the
specific providers where you want fallback or limits.

## Signature

```python
voicegateway.guard(
    provider,                    # a native LiveKit plugin OR Pipecat service
    *,
    fallback: list = (),         # same-framework providers, tried in order on error
    rate_limit: str | None = None,  # e.g. "60/min" or "5/s"
    budget: str | None = None,      # e.g. "$5.00/day" or "$100/month"
    project: str | None = None,     # project id for budget lookups + tagging
)                                # returns the SAME type it wrapped (drop-in)
```

- **fallback**: on a primary-provider error, each fallback runs in order until
  one succeeds. `attach()` stamps `fallback_from=<primary>` and
  `status="fallback"` on the row for the provider that actually produced the
  result. If every provider fails, the last error is re-raised.
- **rate_limit**: a token bucket parsed from the DSL (`"60/min"`, `"5/s"`). When
  the bucket is empty the guarded call raises `RateLimitExceeded`.
- **budget**: a spend cap parsed from the DSL (`"$5.00/day"`, `"$100/month"`).
  The guard reads the window's accumulated spend from the core and raises
  `BudgetExceededError` when it is at or over the cap. Budget enforcement depends
  on the cost data `attach()` writes, which closes the measure-then-enforce loop.

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

    <Tip>
    You can guard any combination of modalities independently. Guard only the
    providers where you actually need fallback or limits; leave the others as plain
    native plugins.
    </Tip>
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

    stt = DeepgramSTTService(api_key=DEEPGRAM_API_KEY)
    guarded_llm = voicegateway.guard(
        OpenAILLMService(api_key=OPENAI_API_KEY, model="gpt-4o-mini"),
        fallback=[OpenAILLMService(api_key=OPENAI_API_KEY, model="gpt-4o")],
        rate_limit="60/min",
        budget="$5.00/day",
    )
    tts = CartesiaTTSService(api_key=CARTESIA_API_KEY, voice_id=VOICE_ID)

    pipeline = Pipeline([transport.input(), stt, guarded_llm, tts, transport.output()])
    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
    )

    voicegateway.attach(task, project="my-agent")
    ```

    ### Pipecat fallback scope

    Fallback on the Pipecat path switches providers **before the first output frame**.
    If the primary service fails to produce its first frame, `guard()` tries the next
    provider. Once the primary has started streaming output, there is **no mid-stream
    recovery**: a failure partway through a response is surfaced, not silently
    patched over by swapping to a fallback.

    <Warning>
    Size a Pipecat fallback for "primary is down / rejecting" rather than "primary
    died halfway through a token stream." Mid-stream failures are not recoverable on
    the Pipecat path.
    </Warning>
  </Tab>
</Tabs>

## What guard() does not do

- It does not manage provider keys or config. You pass native providers you have
  already configured; this fits bring-your-own-keys.
- It does not meter. Cost and latency come from `attach()`.
- It does not abstract across frameworks. `guard()` wraps native providers of one
  framework at a time; a LiveKit guard takes LiveKit fallbacks, a Pipecat guard
  takes Pipecat fallbacks.

## See also

- [Core Concepts](/guide/core-concepts): the two-seam model and how guard and attach coordinate.
- [attach()](/guide/attach): the passive meter guard composes with.
- [Frameworks and extras](/guide/frameworks): the framework-neutral core.
