"""P4 end-to-end: the runnable examples import, and attach() + guard() compose.

Two concerns:

1. **Examples import clean.** ``examples/livekit_attach_guard.py`` and
   ``examples/pipecat_attach_guard.py`` must import without error (their native
   provider imports are deferred into helper functions, so importing the module
   never requires the provider wheels or API keys). This guards the docs'
   copy-paste path against a stale example.

2. **attach() + guard() compose.** A light end-to-end proving the two seams work
   together with no double-count:
   - LiveKit: reuse the fake-session pattern (a real ``livekit.agents`` LLM
     subclass guarded, bound to a MetricCapture via ``attach``); one
     ``metrics_collected`` event yields exactly one row, and a guard fallback is
     stamped ``fallback_from`` by the meter.
   - Pipecat: reuse the P2 e2e pattern (a real pipeline whose emitter is a guarded
     LLM service) and assert ``attach(task)`` produces exactly one record.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from typing import Any

import pytest

# examples/ sits at the repo root: .../voicegateway/examples/*.py. This file is
# at src/voicegateway/tests/inference/, so walk up to the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_EXAMPLES = _REPO_ROOT / "examples"
_LIVEKIT_EXAMPLE = _EXAMPLES / "livekit_attach_guard.py"
_PIPECAT_EXAMPLE = _EXAMPLES / "pipecat_attach_guard.py"


# --- 1. examples import / parse ---------------------------------------------


@pytest.mark.parametrize("path", [_LIVEKIT_EXAMPLE, _PIPECAT_EXAMPLE])
def test_example_parses(path: Path) -> None:
    """Each example is syntactically valid Python."""
    assert path.exists(), f"missing example: {path}"
    ast.parse(path.read_text())


def _import_example(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # ImportError here fails the test
    return module


def test_livekit_example_imports() -> None:
    """The LiveKit example imports (deferred plugin imports keep it clean)."""
    module = _import_example(_LIVEKIT_EXAMPLE)
    # The public entry points exist and are callable.
    assert callable(module.build_session)
    assert callable(module.entrypoint)


def test_pipecat_example_imports() -> None:
    """The Pipecat example imports (deferred service imports keep it clean)."""
    module = _import_example(_PIPECAT_EXAMPLE)
    assert callable(module.build_task)
    assert callable(module.run)


# --- 2a. LiveKit: attach() + guard() compose (fake session) -----------------


class _RecordingSink:
    def __init__(self) -> None:
        self.rows: list[Any] = []

    async def log_request(self, record: Any) -> None:
        self.rows.append(record)

    async def flush(self) -> None:
        pass

    async def aclose(self) -> None:
        pass


async def test_livekit_attach_plus_guard_single_record() -> None:
    """A guard-wrapped LiveKit LLM + attach(session): one metrics event -> one row.

    Proves attach is the single meter (guard adds no second row) with the real
    guard wrapper in the loop, mirroring the P1 no-double-count guarantee.
    """
    lk_llm = pytest.importorskip("livekit.agents.llm")

    import voicegateway
    from voicegateway.inference.session.context import reset_guard_fallback_from

    reset_guard_fallback_from()

    class _FakeLLMStream:
        def __aiter__(self) -> Any:
            return self._agen()

        async def _agen(self) -> Any:
            for token in ("hello", "world"):
                yield token

        async def aclose(self) -> None:
            pass

    class _FakeLLM(lk_llm.LLM):
        def __init__(self, *, model: str, provider: str) -> None:
            super().__init__()
            self._model = model
            self._provider_id = provider
            self._handlers: dict[str, list[Any]] = {}

        @property
        def model(self) -> str:
            return self._model

        @property
        def provider(self) -> str:
            return self._provider_id

        def chat(self, **kwargs: Any) -> Any:
            return _FakeLLMStream()

        def on(self, event: str, handler: Any) -> None:
            self._handlers.setdefault(event, []).append(handler)

        def emit(self, event: str, *args: Any) -> None:
            for handler in list(self._handlers.get(event, [])):
                handler(*args)

    class _Session:
        def __init__(self, *, llm: Any) -> None:
            self.stt = None
            self.llm = llm
            self.tts = None
            self._handlers: dict[str, list[Any]] = {}

        def on(self, event: str, handler: Any) -> None:
            self._handlers.setdefault(event, []).append(handler)

        def emit(self, event: str, *args: Any) -> None:
            for handler in list(self._handlers.get(event, [])):
                handler(*args)

    class _LLMMetric:
        prompt_tokens = 120
        completion_tokens = 40
        prompt_cached_tokens = 0
        ttft = 0.15

    try:
        sink = _RecordingSink()
        inner = _FakeLLM(model="gpt-4o-mini", provider="openai")
        guarded = voicegateway.guard(inner)
        session = _Session(llm=guarded)

        voicegateway.attach(session, project="ex", agent_id="a", sink=sink)

        inner.emit("metrics_collected", _LLMMetric())
        await session._vg_capture.drain()

        assert len(sink.rows) == 1
        assert sink.rows[0].modality == "llm"
        assert sink.rows[0].provider == "openai"
    finally:
        reset_guard_fallback_from()


# --- 2b. Pipecat: attach() + guard() compose (real pipeline) ----------------


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_pipecat_attach_records_in_live_pipeline() -> None:
    """attach(task) meters a live Pipecat pipeline: one MetricsFrame -> one record.

    Reuses the P2 e2e shape (a real ``Pipeline`` / ``PipelineTask`` run to
    completion). This proves ``attach(task)`` (the sole meter) works inside a
    running pipeline. The companion test below proves guard composes with it.
    """
    import asyncio

    pytest.importorskip("pipecat")

    from pipecat.frames.frames import Frame, MetricsFrame, StartFrame
    from pipecat.metrics.metrics import LLMTokenUsage, LLMUsageMetricsData
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.task import PipelineTask
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

    import voicegateway

    class _EmitterLLM(FrameProcessor):
        """A stub processor under a provider-shaped module that emits LLM usage."""

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
                md = LLMUsageMetricsData(
                    processor=self.name, model="gpt-4o", value=usage
                )
                await self.push_frame(MetricsFrame(data=[md]))
            await self.push_frame(frame, direction)

    sink = _RecordingSink()
    pipeline = Pipeline([_EmitterLLM()])
    task = PipelineTask(pipeline)

    session_id = voicegateway.attach(task, project="ex", agent_id="a", sink=sink)
    assert isinstance(session_id, str) and session_id

    runner = PipelineRunner()

    async def _stop() -> None:
        await asyncio.sleep(0.3)
        await task.stop_when_done()

    await asyncio.gather(runner.run(task), _stop())

    llm_rows = [r for r in sink.rows if r.modality == "llm"]
    assert len(llm_rows) == 1, f"expected one llm row, got {len(llm_rows)}"
    row = llm_rows[0]
    assert row.provider == "openai"
    assert row.model_id == "openai/gpt-4o"
    assert row.input_units == 1000.0
    assert row.project == "ex"


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_pipecat_attach_plus_guard_compose_fallback_stamp() -> None:
    """attach()'s observer + guard()'s fallback ContextVar compose on Pipecat.

    guard() is a drop-in ``LLMService`` and writes no metrics; when it falls back
    it sets the routing ContextVar. attach()'s observer (the sole meter) then
    stamps ``fallback_from`` / ``status="fallback"`` on the row it writes for the
    service that actually ran. This is the measure-plus-control loop closing
    through the framework-neutral core, deterministically (no pipeline run).
    """
    pytest.importorskip("pipecat")

    from pipecat.frames.frames import (
        ErrorFrame,
        Frame,
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        LLMTextFrame,
        MetricsFrame,
    )
    from pipecat.metrics.metrics import LLMTokenUsage, LLMUsageMetricsData
    from pipecat.services.llm_service import LLMService

    import voicegateway
    from voicegateway.inference.session.context import (
        current_guard_fallback_from,
        reset_guard_fallback_from,
    )

    reset_guard_fallback_from()

    _cache: dict[str, type] = {}

    class _StubLLM(LLMService):
        def __init__(self, *, provider: str, model: str, fail: bool = False) -> None:
            super().__init__()
            # pipecat_identity reads type(obj).__module__ to resolve the provider,
            # so each stub needs a per-provider subclass under a provider-shaped
            # module (subclassing _StubLLM keeps run_request; a bare LLMService
            # subclass would lose it and raise AttributeError).
            self.__class__ = _provider_llm(provider)
            self._settings.model = model
            self._fail = fail

        async def run_request(self) -> list[Frame]:
            if self._fail:
                return [ErrorFrame(f"{self._settings.model} boom")]
            return [
                LLMFullResponseStartFrame(),
                LLMTextFrame("hi"),
                LLMFullResponseEndFrame(),
            ]

    def _provider_llm(provider: str) -> type:
        cls = _cache.get(provider)
        if cls is None:
            cls = type(f"_StubLLM_{provider}", (_StubLLM,), {})
            cls.__module__ = f"pipecat.services.{provider}.llm"
            _cache[provider] = cls
        return cls

    def _is_output(frame: Frame) -> bool:
        return isinstance(frame, (LLMFullResponseStartFrame, LLMTextFrame))

    sink = _RecordingSink()
    observer = voicegateway.Observer(sink=sink, project="ex", agent_id="a")

    # guard falls back from openai to the service that runs (anthropic).
    ran = _StubLLM(provider="anthropic", model="claude-haiku")
    primary = _StubLLM(provider="openai", model="gpt-4o-mini", fail=True)
    guarded = voicegateway.guard(primary, fallback=[ran])

    async def _invoke(service: Any) -> list[Frame]:
        return await service.run_request()

    try:
        await guarded._control.run_request_with_fallback(
            _invoke, produced_output=_is_output
        )
        # guard set the routing marker to the primary it fell back FROM.
        assert current_guard_fallback_from() == "openai"

        # attach()'s observer meters the run the fallback produced and stamps it.
        usage = LLMTokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        md = LLMUsageMetricsData(processor=ran.name, model="claude-haiku", value=usage)

        class _Pushed:
            frame = MetricsFrame(data=[md])
            source = ran

        await observer.on_push_frame(_Pushed())

        assert len(sink.rows) == 1
        row = sink.rows[0]
        assert row.provider == "anthropic"
        assert row.fallback_from == "openai"
        assert row.status == "fallback"
    finally:
        reset_guard_fallback_from()
