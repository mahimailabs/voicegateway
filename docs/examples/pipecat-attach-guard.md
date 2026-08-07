---
title: "Pipecat: attach + guard"
description: A complete, runnable Pipecat agent. Native services metered by attach(), the LLM guarded with fallback and a daily spend cap.
---
One runnable Pipecat pipeline, on local mic/speaker audio so it needs no
telephony or video setup. Native `pipecat.services` metered by
[`attach()`](/guide/attach) (the single passive meter), with the LLM wrapped by
one [`guard()`](/guide/guard) call for fallback, a rate limit, and a daily
spend cap. The public surface is the same as the
[LiveKit agent](/guide/first-agent); only the providers differ.

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
export OPENAI_API_KEY=... DEEPGRAM_API_KEY=... CARTESIA_API_KEY=... CARTESIA_VOICE_ID=...
```

## agent.py

```python
"""Pipecat voice agent with VoiceGateway cost metering and LLM fallback."""

import asyncio
import os

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams

import voicegateway

PROJECT = "pipecat-demo"
INSTRUCTIONS = (
    "You are a friendly voice assistant. Keep your answers short and clear. "
    "One or two sentences unless asked for more."
)


async def main() -> None:
    transport = LocalAudioTransport(
        LocalAudioTransportParams(audio_in_enabled=True, audio_out_enabled=True)
    )

    stt = DeepgramSTTService(api_key=os.environ["DEEPGRAM_API_KEY"])

    # guard() wraps the LLM with a fallback and a daily budget.
    # It returns a drop-in OpenAILLMService, so the Pipeline sees no difference.
    llm = voicegateway.guard(
        OpenAILLMService(
            api_key=os.environ["OPENAI_API_KEY"],
            settings=OpenAILLMService.Settings(
                model="gpt-4o-mini", system_instruction=INSTRUCTIONS
            ),
        ),
        fallback=[
            OpenAILLMService(
                api_key=os.environ["OPENAI_API_KEY"],
                settings=OpenAILLMService.Settings(
                    model="gpt-4o", system_instruction=INSTRUCTIONS
                ),
            )
        ],
        rate_limit="60/min",
        budget="$5.00/day",
        project=PROJECT,
    )

    tts = CartesiaTTSService(
        api_key=os.environ["CARTESIA_API_KEY"],
        settings=CartesiaTTSService.Settings(
            model="sonic-3", voice=os.environ["CARTESIA_VOICE_ID"]
        ),
    )

    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            llm,
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )

    task = PipelineTask(
        pipeline,
        # Pipecat emits the usage frames the meter reads only when these are on.
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
    )

    # attach() is the single meter. Call it once, before runner.run(task).
    voicegateway.attach(task, project=PROJECT)

    runner = PipelineRunner()
    await runner.run(task)


if __name__ == "__main__":
    asyncio.run(main())
```

## Run

```bash
python agent.py
```

Speak into your microphone; the pipeline replies through your speakers. Costs
and latency land in the dashboard under the `pipecat-demo` project at
`http://localhost:8080`.

<Note>
`PipelineRunner` is deprecated upstream since pipecat-ai 1.3.0 in favor of
`WorkerRunner`, but stays functional through the 1.x series (removal is
planned for 2.0). VoiceGateway's `attach()` calls `task.add_observer(...)`,
which needs a `PipelineTask`, so keep `PipelineTask` / `PipelineRunner` (not
`PipelineWorker` / `WorkerRunner`) until VoiceGateway's Pipecat integration is
updated.
</Note>

## attach() vs Observer

Both do the same thing; pick whichever reads cleaner:

```python
# After construction (used above):
voicegateway.attach(task, project=PROJECT)

# Or in the constructor:
task = PipelineTask(pipeline, params=params, observers=[voicegateway.Observer(project=PROJECT)])
```

## Fallback scope on Pipecat

`guard()` fallback on Pipecat switches providers **before the first output
frame**. If the primary fails to produce its first frame, the fallback runs.
Once the primary has started streaming output there is **no mid-stream
recovery**. See [guard()](/guide/guard) for the full fallback scope on each
framework.

## Notes

- The service constructors above use each service's `settings=` object
  (`OpenAILLMService.Settings`, `CartesiaTTSService.Settings`) rather than the
  older flat `model=` / `voice_id=` kwargs, which pipecat-ai deprecated in
  favor of `settings=`.
- A real deployment swaps `LocalAudioTransport` for a WebRTC or telephony
  transport (Daily, a SIP serializer, etc.). The transport also drives
  `attach()`'s channel auto-detect (telephony vs. web).
