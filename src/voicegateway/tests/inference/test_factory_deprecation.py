"""The LLM/STT/TTS factories are deprecated shims: they still work, warn once,
and no longer meter (so LLM()+attach() does not double-count)."""

from __future__ import annotations

import warnings
from typing import Any

import pytest
import yaml

from voicegateway.core import gateway_factory as factory
from voicegateway.inference import base_inference
from voicegateway.inference import llm_inference as llm


class _FakeLLM:
    """Pretends to be a livekit.plugins.<provider>.LLM instance (event emitter)."""

    def __init__(self, model: str, **kwargs: Any) -> None:
        self.model = model
        self.provider = "openai"
        self.kwargs = kwargs
        self._handlers: dict[str, list[Any]] = {}

    def on(self, event: str, callback: Any) -> None:
        self._handlers.setdefault(event, []).append(callback)

    def emit(self, event: str, *args: Any) -> None:
        for handler in list(self._handlers.get(event, [])):
            handler(*args)

    async def aclose(self) -> None:
        pass


class _FakeProvider:
    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config

    def create_llm(self, model: str, **kwargs: Any) -> _FakeLLM:
        return _FakeLLM(model, **kwargs)


@pytest.fixture
def fake_provider(monkeypatch):
    def _create(provider_name: str, config: dict[str, Any]) -> _FakeProvider:
        return _FakeProvider(config)

    monkeypatch.setattr("voicegateway.core.registry.create_provider", _create)


@pytest.fixture
def configured_gateway(tmp_path, monkeypatch):
    cfg = {
        "providers": {"openai": {"api_key": "k"}},
        "models": {"stt": {}, "llm": {}, "tts": {}},
        "stacks": {},
        "fallbacks": {"stt": [], "llm": [], "tts": []},
        "cost_tracking": {"enabled": False},
        "observability": {"latency_tracking": True},
    }
    config_path = tmp_path / "voicegw.yaml"
    config_path.write_text(yaml.dump(cfg))
    from voicegateway.core.gateway import Gateway

    gw = Gateway(config_path=str(config_path))
    monkeypatch.setattr(factory, "_gateway", gw)
    return gw


@pytest.fixture(autouse=True)
def _reset_deprecation_flag(monkeypatch):
    # Reset the once-per-process guard so each test can observe the warning.
    monkeypatch.setattr(base_inference, "_deprecation_emitted", False)
    yield


def test_llm_factory_emits_deprecation_warning(configured_gateway, fake_provider):
    with pytest.warns(
        DeprecationWarning, match="voicegateway.LLM/STT/TTS are deprecated"
    ):
        result = llm.LLM("openai/gpt-4o-mini")
    # Still returns a usable, drop-in object.
    assert result.__class__.__name__ == "InstrumentedLLM"
    assert result.model == "gpt-4o-mini"


def test_deprecation_warning_fires_only_once(configured_gateway, fake_provider):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        llm.LLM("openai/gpt-4o-mini")
        llm.LLM("openai/gpt-4o-mini")
    dep = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(dep) == 1, "the factory deprecation warning must fire at most once"


def test_factory_wrapper_does_not_meter(configured_gateway, fake_provider):
    """The factory wrapper is metering=False: it does not subscribe its own
    metering handler to the inner plugin."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        result = llm.LLM("openai/gpt-4o-mini")
    assert object.__getattribute__(result, "_metering") is False


async def test_no_double_count_factory_plus_attach(
    configured_gateway, fake_provider, tmp_path
):
    """A component produced by LLM() + attach(session): one metrics_collected
    event yields exactly ONE RequestRecord (attach meters; the factory does not)."""
    import voicegateway

    class _RecordingSink:
        def __init__(self) -> None:
            self.rows: list = []

        async def log_request(self, record: Any) -> None:
            self.rows.append(record)

        async def flush(self) -> None:
            pass

        async def aclose(self) -> None:
            pass

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
        prompt_tokens = 100
        completion_tokens = 50
        prompt_cached_tokens = 0
        ttft = 0.2

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        wrapper = llm.LLM("openai/gpt-4o-mini")

    inner = object.__getattribute__(wrapper, "_wrapped")
    session = _Session(llm=wrapper)
    sink = _RecordingSink()
    voicegateway.attach(session, project="p", agent_id="a", sink=sink)

    # Fire ONE metric on the inner plugin. The factory wrapper forwards it
    # transparently; attach (bound to session.llm == wrapper) meters it once.
    inner.emit("metrics_collected", _LLMMetric())
    await session._vg_capture.drain()

    assert len(sink.rows) == 1, f"expected exactly one row, got {len(sink.rows)}"
    assert sink.rows[0].modality == "llm"
