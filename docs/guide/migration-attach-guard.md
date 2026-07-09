---
title: Migrate to attach() + guard()
description: Move off the deprecated voicegateway.LLM/STT/TTS factories to native providers plus attach() (and guard() for fallback and limits), on LiveKit and Pipecat.
---

# Migrate to attach() + guard()

The `voicegateway.LLM("openai/gpt-4o")` / `STT(...)` / `TTS(...)` factories are
**deprecated**. They still work and still meter, but they emit a
`DeprecationWarning` and will be removed in a future release. This guide moves you
to the framework-neutral shape: **native providers + `attach()`**, plus
[`guard()`](/guide/guard) where you had fallback or limits.

## Why the change

- **No more double-count.** The old factories and `attach()` both subscribed to
  the same metrics events, so using `voicegateway.LLM()` together with
  `attach(session)` counted every call twice. `attach()` is now the single meter,
  so this class of bug is gone.
- **Bring-your-own-keys, native providers.** You construct native
  `livekit.plugins.*` or `pipecat.services.*` providers with your own keys and
  settings, exactly as the framework documents them. VoiceGateway observes and
  (optionally) controls them instead of re-homing their configuration.
- **Framework-neutral.** The same `attach()` / `guard()` surface works on both
  LiveKit and Pipecat, and `import voicegateway` pulls neither framework.

## The mapping

| Old | New |
|---|---|
| `voicegateway.LLM("openai/gpt-4o")` | native `openai.LLM(model="gpt-4o")` + `attach(session)` |
| `voicegateway.STT("deepgram/nova-3")` | native `deepgram.STT(model="nova-3")` + `attach(session)` |
| `voicegateway.TTS("cartesia/sonic-3")` | native `cartesia.TTS(model="sonic-3")` + `attach(session)` |
| a factory with `fallback=[...]` | `voicegateway.guard(provider, fallback=[...])` |
| per-project daily budget / rate limits | `voicegateway.guard(provider, budget="$5.00/day", rate_limit="60/min")` |

The single rule: **observability moves to `attach()`, control moves to
`guard()`.** Metering happens once (in `attach`) regardless of how many providers
you guard.

## LiveKit

Before:

```python
from livekit.agents import Agent, AgentSession
from voicegateway import LLM, STT, TTS  # deprecated

session = AgentSession(
    stt=STT("deepgram/nova-3"),
    llm=LLM("openai/gpt-4o-mini"),
    tts=TTS("cartesia/sonic-3"),
)
await session.start(agent=Agent(instructions="Be helpful."), room=ctx.room)
```

After:

```python
from livekit.agents import Agent, AgentSession
from livekit.plugins import deepgram, openai, cartesia

import voicegateway

session = AgentSession(
    stt=deepgram.STT(model="nova-3"),
    llm=openai.LLM(model="gpt-4o-mini"),
    tts=cartesia.TTS(model="sonic-3"),
)

# One meter for the whole session. No double-count.
voicegateway.attach(session, project="my-agent")

await session.start(agent=Agent(instructions="Be helpful."), room=ctx.room)
```

If a modality had fallback or limits, wrap just that provider:

```python
llm=voicegateway.guard(
    openai.LLM(model="gpt-4o-mini"),
    fallback=[openai.LLM(model="gpt-4o")],
    rate_limit="60/min",
    budget="$5.00/day",
),
```

## Pipecat

The old factories were LiveKit-only. On Pipecat you construct native
`pipecat.services.*` and observe them the same way. Enable Pipecat's metrics so
there is usage for `attach()` to record:

```python
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.cartesia.tts import CartesiaTTSService

import voicegateway

stt = DeepgramSTTService(api_key=DEEPGRAM_API_KEY)
llm = voicegateway.guard(
    OpenAILLMService(api_key=OPENAI_API_KEY, model="gpt-4o-mini"),
    fallback=[OpenAILLMService(api_key=OPENAI_API_KEY, model="gpt-4o")],
    budget="$5.00/day",
)
tts = CartesiaTTSService(api_key=CARTESIA_API_KEY, voice_id=VOICE_ID)

pipeline = Pipeline([transport.input(), stt, llm, tts, transport.output()])
task = PipelineTask(
    pipeline,
    params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
)

voicegateway.attach(task, project="my-agent")
```

## Install the right extra

The factories pulled `livekit` implicitly. Now install the extra for the
framework you run:

```bash
pip install "voicegateway[livekit]"   # LiveKit Agents
pip install "voicegateway[pipecat]"   # Pipecat
```

See [Frameworks and extras](/guide/frameworks) for the details.

## Silencing the warning during migration

While you migrate, the deprecated factories keep working. If a
`DeprecationWarning` breaks a strict test run, filter it narrowly until you have
moved that call site over:

```python
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning, module="voicegateway")
```

## See also

- [attach()](/guide/attach): the single meter.
- [guard()](/guide/guard): fallback, rate limits, and spend caps.
- [Frameworks and extras](/guide/frameworks): the framework-neutral core and
  extras.
