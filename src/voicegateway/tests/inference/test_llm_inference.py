"""Tests for voicegateway.inference.llm_inference.LLM."""

from __future__ import annotations

import contextvars
from typing import Any

import pytest
import yaml

from voicegateway.core import gateway_factory as factory
from voicegateway.core.model_resolution import ModelResolutionError
from voicegateway.inference import llm_inference as llm
from voicegateway.inference.session.context import get_session_id

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeLLM:
    """Pretends to be a livekit.plugins.<provider>.LLM instance."""

    def __init__(self, model: str, **kwargs: Any) -> None:
        self.model = model
        self.kwargs = kwargs

    def on(self, event: str, callback: Any) -> None:
        # No-op for tests; the bridge in InstrumentedLLM registers a
        # listener but the fake never fires events.
        pass

    async def aclose(self) -> None:
        pass


class _FakeProvider:
    """A BaseProvider-like fake whose create_llm records the call."""

    last_config: dict[str, Any] | None = None
    last_create_llm: dict[str, Any] | None = None
    last_provider_name: str | None = None

    def __init__(self, config: dict[str, Any]) -> None:
        _FakeProvider.last_config = dict(config)
        self._config = config

    def create_stt(self, model: str, **kwargs: Any) -> Any:
        raise NotImplementedError

    def create_llm(self, model: str, **kwargs: Any) -> _FakeLLM:
        _FakeProvider.last_create_llm = {"model": model, **kwargs}
        return _FakeLLM(model, **kwargs)

    def create_tts(self, model: str, voice: str | None = None, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def health_check(self) -> bool:
        return True


@pytest.fixture
def fake_provider(monkeypatch):
    _FakeProvider.last_config = None
    _FakeProvider.last_create_llm = None
    _FakeProvider.last_provider_name = None

    def _create(provider_name: str, config: dict[str, Any]) -> _FakeProvider:
        _FakeProvider.last_provider_name = provider_name
        return _FakeProvider(config)

    monkeypatch.setattr("voicegateway.core.registry.create_provider", _create)
    return _FakeProvider


@pytest.fixture
def configured_gateway(tmp_path, monkeypatch):
    cfg = {
        "providers": {
            "openai": {"api_key": "from-yaml-openai-key"},
            "anthropic": {"api_key": "from-yaml-anthropic-key"},
            "ollama": {"api_key": ""},
        },
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestConstructorAcceptsLkSignatureParams:
    def test_minimal_call_with_only_model(self, configured_gateway, fake_provider):
        result = llm.LLM("openai/gpt-4o-mini")
        assert result.__class__.__name__ == "InstrumentedLLM"
        assert result._wrapped.model == "gpt-4o-mini"

    def test_all_lk_params_accepted(self, configured_gateway, fake_provider):
        result = llm.LLM(
            "openai/gpt-4o-mini",
            provider="openai",
            base_url="https://example.test",
            api_key="override-key",
            extra_kwargs={"temperature": 0.2},
        )
        assert result.__class__.__name__ == "InstrumentedLLM"


class TestModelResolution:
    def test_provider_kwarg_precedence(self, configured_gateway, fake_provider):
        # Explicit provider="openai" overrides any leading "anthropic/" segment
        # in the model string. The model string is used verbatim minus the
        # leading provider prefix.
        llm.LLM("anthropic/claude-3", provider="openai")
        assert fake_provider.last_provider_name == "openai"
        assert fake_provider.last_create_llm["model"] == "claude-3"

    def test_provider_kwarg_with_no_slash_in_model(
        self, configured_gateway, fake_provider
    ):
        llm.LLM("gpt-4o-mini", provider="openai")
        assert fake_provider.last_provider_name == "openai"
        assert fake_provider.last_create_llm["model"] == "gpt-4o-mini"

    def test_model_string_parsed_when_provider_omitted(
        self, configured_gateway, fake_provider
    ):
        llm.LLM("openai/gpt-4o-mini")
        assert fake_provider.last_provider_name == "openai"
        assert fake_provider.last_create_llm["model"] == "gpt-4o-mini"

    def test_internal_colons_preserved_for_ollama_tags(
        self, configured_gateway, fake_provider
    ):
        # LLM does NOT have STT/TTS's colon-suffix convention. Ollama
        # tags like ":3b" must arrive at the plugin verbatim.
        llm.LLM("ollama/qwen2.5:3b")
        assert fake_provider.last_create_llm["model"] == "qwen2.5:3b"

    def test_missing_model_raises(self, configured_gateway, fake_provider):
        with pytest.raises(TypeError):
            llm.LLM()  # type: ignore[call-arg]

    def test_empty_model_raises(self, configured_gateway, fake_provider):
        with pytest.raises(ValueError, match="non-empty model string"):
            llm.LLM("")

    def test_unknown_provider_raises(self, configured_gateway, fake_provider):
        with pytest.raises(ModelResolutionError, match="Unknown provider"):
            llm.LLM("not-a-real-co/foo")


class TestKwargForwarding:
    def test_extra_kwargs_spread_to_create_llm(self, configured_gateway, fake_provider):
        llm.LLM(
            "openai/gpt-4o-mini",
            extra_kwargs={"temperature": 0.2, "max_tokens": 1024},
        )
        assert fake_provider.last_create_llm["temperature"] == 0.2
        assert fake_provider.last_create_llm["max_tokens"] == 1024

    def test_base_url_forwarded(self, configured_gateway, fake_provider):
        llm.LLM("openai/gpt-4o-mini", base_url="https://my-proxy.test")
        assert fake_provider.last_create_llm["base_url"] == "https://my-proxy.test"

    def test_omitted_params_not_forwarded(self, configured_gateway, fake_provider):
        llm.LLM("openai/gpt-4o-mini")
        # Only `model` should appear; None defaults must NOT leak as
        # explicit kwargs.
        assert set(fake_provider.last_create_llm) == {"model"}


class TestApiKeyOverride:
    def test_override_replaces_yaml_key(self, configured_gateway, fake_provider):
        llm.LLM("openai/gpt-4o-mini", api_key="per-call-override")
        assert fake_provider.last_config["api_key"] == "per-call-override"

    def test_no_override_uses_yaml_key(self, configured_gateway, fake_provider):
        llm.LLM("openai/gpt-4o-mini")
        assert fake_provider.last_config["api_key"] == "from-yaml-openai-key"


class TestSessionCorrelation:
    def test_construction_creates_session_id(self, configured_gateway, fake_provider):
        def _scenario():
            assert get_session_id() is None
            llm.LLM("openai/gpt-4o-mini")
            return get_session_id()

        sid = contextvars.copy_context().run(_scenario)
        assert sid is not None
        assert sid.startswith("vg-")

    def test_two_constructions_share_session_id(
        self, configured_gateway, fake_provider
    ):
        def _scenario():
            llm.LLM("openai/gpt-4o-mini")
            first = get_session_id()
            llm.LLM("openai/gpt-4o-mini")
            second = get_session_id()
            return first, second

        a, b = contextvars.copy_context().run(_scenario)
        assert a is not None
        assert a == b


class TestUnsupportedParamRejected:
    def test_api_secret_no_longer_a_kwarg(self, configured_gateway, fake_provider):
        with pytest.raises(TypeError):
            llm.LLM("openai/gpt-4o-mini", api_secret="ignored")

    def test_inference_class_no_longer_a_kwarg(self, configured_gateway, fake_provider):
        with pytest.raises(TypeError):
            llm.LLM("openai/gpt-4o-mini", inference_class="standard")
