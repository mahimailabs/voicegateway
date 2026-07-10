---
title: "Pipecat: attach + guard"
description: Native Pipecat services metered by an Observer with a guarded LLM providing fallback and a daily spend cap.
---

# Pipecat: attach + guard

A minimal Pipecat pipeline built from native `pipecat.services`, metered by an
[`Observer`](/guide/attach) (the single passive meter) and controlled by one
[`guard()`](/guide/guard) wrapper around the LLM (fallback and a
daily spend cap). The public surface is identical to the
[LiveKit example](/examples/livekit-attach-guard); only the providers differ.

The full runnable file lives at
[`examples/pipecat_attach_guard.py`](https://github.com/mahimailabs/voicegateway/blob/main/examples/pipecat_attach_guard.py).

## Install

<CodeGroup>
```bash uv
uv add "voicegateway[pipecat]" "pipecat-ai[openai,deepgram,cartesia,silero]"
```

```bash pip
pip install "voicegateway[pipecat]" "pipecat-ai[openai,deepgram,cartesia,silero]"
```
</CodeGroup>

```bash
export OPENAI_API_KEY=... DEEPGRAM_API_KEY=... CARTESIA_API_KEY=...
```

## The pipeline

```python
import os

import voicegateway

PROJECT = "pipecat-demo"


def build_task(transport_input, transport_output):
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.task import PipelineParams, PipelineTask
    from pipecat.services.cartesia.tts import CartesiaTTSService
    from pipecat.services.deepgram.stt import DeepgramSTTService
    from pipecat.services.openai.llm import OpenAILLMService

    stt = DeepgramSTTService(api_key=os.environ["DEEPGRAM_API_KEY"])
    # guard() wraps ONE service for control; it returns a drop-in service.
    llm = voicegateway.guard(
        OpenAILLMService(api_key=os.environ["OPENAI_API_KEY"], model="gpt-4o-mini"),
        fallback=[
            OpenAILLMService(api_key=os.environ["OPENAI_API_KEY"], model="gpt-4o")
        ],
        budget="$5.00/day",
        project=PROJECT,
    )
    tts = CartesiaTTSService(api_key=os.environ["CARTESIA_API_KEY"])

    pipeline = Pipeline([transport_input, stt, llm, tts, transport_output])

    return PipelineTask(
        pipeline,
        # Pipecat emits the usage MetricsFrames the meter reads only when these
        # are on; without them there is nothing for attach() to record.
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        # Attach the single meter via the exported Observer. Equivalent to
        # voicegateway.attach(task, project=PROJECT) after construction.
        observers=[voicegateway.Observer(project=PROJECT)],
    )
```

## Enable Pipecat metrics

The observer meters `MetricsFrame`s, so the task must emit them:

```python
from pipecat.pipeline.task import PipelineParams

params = PipelineParams(enable_metrics=True, enable_usage_metrics=True)
```

Without `enable_metrics` / `enable_usage_metrics`, Pipecat produces no usage
frames and `attach()` records nothing.

## attach() vs Observer

Both do the same thing; pick whichever reads cleaner:

```python
# In the constructor:
task = PipelineTask(pipeline, params=params, observers=[voicegateway.Observer(project=PROJECT)])

# Or after construction:
voicegateway.attach(task, project=PROJECT)
```

## Fallback scope on Pipecat

`guard()` fallback on Pipecat switches providers **before the first output
frame**. If the primary fails to produce its first frame, the fallback runs.
Once the primary has started streaming output there is **no mid-stream
recovery**. See [Pipecat fallback scope](/guide/guard#pipecat-fallback-scope).

## Notes

- The `pipecat.services` imports are inside `build_task()` so importing the
  module never requires the service extras; only running the pipeline does.
- A real deployment wires a Pipecat transport (Daily, WebRTC, or a telephony
  serializer) and passes `transport.input()` / `transport.output()` into the
  pipeline. The transport also drives `attach()`'s channel auto-detect
  (telephony vs web).
