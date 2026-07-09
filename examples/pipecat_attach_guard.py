"""Minimal Pipecat pipeline: native services + voicegateway.attach() + guard().

Shows the framework-neutral shape on Pipecat (the SAME public surface as the
LiveKit example):

- Build a ``Pipeline`` / ``PipelineTask`` from native ``pipecat.services``.
- Wrap ONE service (the LLM) with ``voicegateway.guard(...)`` for fallback and a
  daily spend cap. ``guard`` returns a drop-in of the same Pipecat type.
- Enable Pipecat's metrics on the task (``PipelineParams(enable_metrics=True,
  enable_usage_metrics=True)``) so there is usage to observe, then attach the
  meter with ``PipelineTask(observers=[voicegateway.Observer(...)])`` (shown
  here) or the equivalent ``voicegateway.attach(task)``.
- ``guard`` writes no metrics, so ``attach`` + ``guard`` never double-count.

Run shape (this file is import-clean without the Pipecat service extras or keys;
the native ``pipecat.services`` imports are deferred into ``build_task`` so
importing the module does not require them)::

    pip install "voicegateway[pipecat]" "pipecat-ai[openai,deepgram,cartesia,silero]"
    export OPENAI_API_KEY=... DEEPGRAM_API_KEY=... CARTESIA_API_KEY=...
    python examples/pipecat_attach_guard.py

Running it also needs a transport (Daily / WebRTC / telephony) wired into the
pipeline; this example keeps the transport out so the shape stays minimal.
"""

from __future__ import annotations

import os
from typing import Any

import voicegateway

PROJECT = "pipecat-demo"


def build_task(transport_input: Any, transport_output: Any) -> Any:
    """Build a ``PipelineTask`` with native services and one guarded LLM.

    The native service imports live here (not at module scope) so importing this
    example never requires the Pipecat service extras; only running
    ``build_task(...)`` does. ``transport_input`` / ``transport_output`` are the
    frame processors from your chosen Pipecat transport.
    """
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.task import PipelineParams, PipelineTask
    from pipecat.services.cartesia.tts import CartesiaTTSService
    from pipecat.services.deepgram.stt import DeepgramSTTService
    from pipecat.services.openai.llm import OpenAILLMService

    stt = DeepgramSTTService(api_key=os.environ.get("DEEPGRAM_API_KEY", ""))
    # guard() wraps the LLM for control: on a pre-first-output error it falls
    # back to gpt-4o and blocks past $5.00/day. It returns a drop-in service.
    llm = voicegateway.guard(
        OpenAILLMService(
            api_key=os.environ.get("OPENAI_API_KEY", ""), model="gpt-4o-mini"
        ),
        fallback=[
            OpenAILLMService(
                api_key=os.environ.get("OPENAI_API_KEY", ""), model="gpt-4o"
            )
        ],
        budget="$5.00/day",
        project=PROJECT,
    )
    tts = CartesiaTTSService(api_key=os.environ.get("CARTESIA_API_KEY", ""))

    pipeline = Pipeline([transport_input, stt, llm, tts, transport_output])

    # enable_metrics + enable_usage_metrics make Pipecat emit the MetricsFrames
    # the observer meters; without them there is nothing for attach() to record.
    return PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        # attach() the single meter via the exported Observer. Equivalent to
        # calling voicegateway.attach(task, project=PROJECT) after construction.
        observers=[voicegateway.Observer(project=PROJECT)],
    )


async def run(transport_input: Any, transport_output: Any) -> None:
    """Build the task and run it to completion through the Pipecat runner."""
    from pipecat.pipeline.runner import PipelineRunner

    task = build_task(transport_input, transport_output)
    await PipelineRunner().run(task)


def main() -> None:
    """Placeholder entry: wire your transport and call ``run(...)``.

    A real deployment constructs a Pipecat transport (Daily, WebRTC, or a
    telephony serializer) and passes ``transport.input()`` / ``transport.output()``
    into ``run``. The transport choice also drives attach()'s channel auto-detect
    (telephony vs web).
    """
    raise SystemExit(
        "Wire a Pipecat transport and call run(transport.input(), "
        "transport.output()). See the module docstring."
    )


if __name__ == "__main__":
    main()
