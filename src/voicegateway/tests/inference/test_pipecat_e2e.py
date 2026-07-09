"""Minimal end-to-end Pipecat pipeline test for ``attach()`` / ``Observer``.

Builds a tiny ``Pipeline`` + ``PipelineTask`` with a stub service that emits a
real ``MetricsFrame`` during the run, registers a ``VoiceGatewayObserver`` via
``attach(task)``, runs the task to completion, and asserts a ``RequestRecord``
was produced. This exercises the full observer wiring inside a live pipeline
(not just direct ``on_push_frame`` calls), proving the P2 attach path works.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

pipecat = pytest.importorskip("pipecat")

from pipecat.frames.frames import Frame, MetricsFrame, StartFrame  # noqa: E402
from pipecat.metrics.metrics import LLMTokenUsage, LLMUsageMetricsData  # noqa: E402
from pipecat.pipeline.pipeline import Pipeline  # noqa: E402
from pipecat.pipeline.runner import PipelineRunner  # noqa: E402
from pipecat.pipeline.task import PipelineTask  # noqa: E402
from pipecat.processors.frame_processor import (  # noqa: E402
    FrameDirection,
    FrameProcessor,
)

import voicegateway  # noqa: E402


class _RecordingSink:
    def __init__(self) -> None:
        self.records: list[Any] = []
        self.flushed = 0

    async def log_request(self, record: Any) -> None:
        self.records.append(record)

    async def flush(self) -> None:
        self.flushed += 1


class _EmitterLLM(FrameProcessor):
    """A stub processor whose module emulates a real pipecat LLM service.

    On the pipeline StartFrame it emits one ``MetricsFrame`` carrying LLM usage,
    exactly as a real ``LLMService`` would after a completion.
    """

    # Emulate a real provider service under pipecat.services.openai.llm.
    __module__ = "pipecat.services.openai.llm"

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, StartFrame):
            usage = LLMTokenUsage(
                prompt_tokens=1000,
                completion_tokens=500,
                total_tokens=1500,
                cache_read_input_tokens=200,
            )
            md = LLMUsageMetricsData(processor=self.name, model="gpt-4o", value=usage)
            await self.push_frame(MetricsFrame(data=[md]))
        await self.push_frame(frame, direction)


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_end_to_end_pipeline_produces_record() -> None:
    sink = _RecordingSink()
    emitter = _EmitterLLM()
    pipeline = Pipeline([emitter])
    task = PipelineTask(pipeline)

    # attach() dispatches to the Pipecat path and registers the observer.
    session_id = voicegateway.attach(
        task, project="e2e", agent_id="agent-e2e", sink=sink
    )
    assert isinstance(session_id, str) and session_id

    runner = PipelineRunner()

    async def _stop() -> None:
        await asyncio.sleep(0.3)
        await task.stop_when_done()

    await asyncio.gather(runner.run(task), _stop())

    # Exactly one LLM record despite the frame being re-pushed by wrappers.
    llm_records = [r for r in sink.records if r.modality == "llm"]
    assert len(llm_records) == 1
    rec = llm_records[0]
    assert rec.provider == "openai"
    assert rec.model_id == "openai/gpt-4o"
    assert rec.input_units == 1000.0
    assert rec.output_units == 500.0
    assert rec.cached_input_units == 200.0
    assert rec.cost_usd == pytest.approx(0.00725, rel=1e-6)
    assert rec.project == "e2e"
    assert rec.agent_id == "agent-e2e"


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_observer_export_usable_in_task_constructor() -> None:
    """``voicegateway.Observer`` slots into ``PipelineTask(observers=[...])``."""
    sink = _RecordingSink()
    observer = voicegateway.Observer(sink=sink, project="ctor", agent_id="a")
    emitter = _EmitterLLM()
    pipeline = Pipeline([emitter])
    task = PipelineTask(pipeline, observers=[observer])

    runner = PipelineRunner()

    async def _stop() -> None:
        await asyncio.sleep(0.3)
        await task.stop_when_done()

    await asyncio.gather(runner.run(task), _stop())

    llm_records = [r for r in sink.records if r.modality == "llm"]
    assert len(llm_records) == 1
    assert llm_records[0].model_id == "openai/gpt-4o"
