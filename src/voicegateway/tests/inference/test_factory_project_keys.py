"""Tests for per-project provider key resolution in inference factories."""

from __future__ import annotations

from typing import Any

import pytest
import yaml

from voicegateway.core import gateway_factory as factory
from voicegateway.inference import llm_inference as llm, project, stt_inference as stt, tts_inference as tts


class _FakeProvider:
    """Records the config dict its constructor receives."""

    last_config: dict[str, Any] | None = None

    def __init__(self, config: dict[str, Any]) -> None:
        _FakeProvider.last_config = dict(config)
        self._config = config

    def create_stt(self, model: str, **kwargs: Any) -> Any:
        return _Stub(model, **kwargs)

    def create_llm(self, model: str, **kwargs: Any) -> Any:
        return _Stub(model, **kwargs)

    def create_tts(self, model: str, voice: str | None = None, **kwargs: Any) -> Any:
        return _Stub(model, voice=voice, **kwargs)

    async def health_check(self) -> bool:
        return True


class _Stub:
    """Provider-side stub the wrapper subclasses receive at construction."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        from livekit.agents.stt import STTCapabilities
        from livekit.agents.tts import TTSCapabilities

        # Carry both — the wrapper for the modality picks the one its
        # super().__init__ reads. STTCapabilities has the wider field
        # set, so default to it; TTS-flavoured _Stubs swap in the TTS
        # one via the test's create_tts path if needed.
        self.capabilities: Any = STTCapabilities(streaming=False, interim_results=False)
        self._tts_capabilities: Any = TTSCapabilities(streaming=False)
        self.sample_rate = 24000
        self.num_channels = 1
        self.args = args
        self.kwargs = kwargs

    def on(self, _event: str, _cb: Any) -> None:
        # No-op; the wrapper bridges events but the stub never emits.
        pass


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    monkeypatch.delenv("VOICEGW_ACTIVE_PROJECT", raising=False)
    project.reset_project()
    yield
    project.reset_project()


@pytest.fixture
def fake_providers(monkeypatch):
    _FakeProvider.last_config = None

    def _create(_provider_name: str, config: dict[str, Any]) -> _FakeProvider:
        return _FakeProvider(config)

    monkeypatch.setattr("voicegateway.core.registry.create_provider", _create)
    return _FakeProvider


@pytest.fixture
def gateway_with_per_project_keys(tmp_path, monkeypatch):
    cfg = {
        "providers": {
            "openai": {"api_key": "global-openai-key"},
            "deepgram": {"api_key": "global-dg-key"},
            "cartesia": {"api_key": "global-cartesia-key"},
        },
        "projects": {
            "tony-pizza": {
                "name": "Tony",
                "providers": {
                    "openai": {"api_key": "tony-openai-key"},
                    "deepgram": {"api_key": "tony-dg-key"},
                    "cartesia": {"api_key": "tony-cartesia-key"},
                },
            },
            "mama-diner": {
                "name": "Mama",
                "providers": {
                    "openai": {"api_key": "mama-openai-key"},
                    # mama-diner does NOT override deepgram or cartesia
                },
            },
        },
        "default_project": "mama-diner",
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
# default_project resolution
# ---------------------------------------------------------------------------


def test_stt_uses_default_project_keys(gateway_with_per_project_keys, fake_providers):
    """default_project=mama-diner. mama overrides openai but not"""
    stt.STT("deepgram/nova-3")
    assert fake_providers.last_config["api_key"] == "global-dg-key"


def test_llm_uses_default_project_overridden_key(
    gateway_with_per_project_keys, fake_providers
):
    """LLM(openai/...) under default_project=mama-diner picks up"""
    llm.LLM("openai/gpt-4o-mini")
    assert fake_providers.last_config["api_key"] == "mama-openai-key"


def test_tts_uses_default_project_keys(gateway_with_per_project_keys, fake_providers):
    tts.TTS("cartesia/sonic-3")
    # mama-diner does not override cartesia → falls back to global.
    assert fake_providers.last_config["api_key"] == "global-cartesia-key"


# ---------------------------------------------------------------------------
# set_project()
# ---------------------------------------------------------------------------


def test_stt_uses_set_project_overridden_key(
    gateway_with_per_project_keys, fake_providers
):
    project.set_project("tony-pizza")
    stt.STT("deepgram/nova-3")
    assert fake_providers.last_config["api_key"] == "tony-dg-key"


def test_llm_uses_set_project_overridden_key(
    gateway_with_per_project_keys, fake_providers
):
    project.set_project("tony-pizza")
    llm.LLM("openai/gpt-4o-mini")
    assert fake_providers.last_config["api_key"] == "tony-openai-key"


def test_tts_uses_set_project_overridden_key(
    gateway_with_per_project_keys, fake_providers
):
    project.set_project("tony-pizza")
    tts.TTS("cartesia/sonic-3")
    assert fake_providers.last_config["api_key"] == "tony-cartesia-key"


# ---------------------------------------------------------------------------
# api_key per-call override still wins
# ---------------------------------------------------------------------------


def test_stt_api_key_kwarg_beats_project_override(
    gateway_with_per_project_keys, fake_providers
):
    project.set_project("tony-pizza")
    stt.STT("deepgram/nova-3", api_key="per-call-dg-override")
    assert fake_providers.last_config["api_key"] == "per-call-dg-override"


def test_llm_api_key_kwarg_beats_project_override(
    gateway_with_per_project_keys, fake_providers
):
    project.set_project("tony-pizza")
    llm.LLM("openai/gpt-4o-mini", api_key="per-call-openai-override")
    assert fake_providers.last_config["api_key"] == "per-call-openai-override"


def test_tts_api_key_kwarg_beats_project_override(
    gateway_with_per_project_keys, fake_providers
):
    project.set_project("tony-pizza")
    tts.TTS("cartesia/sonic-3", api_key="per-call-cartesia-override")
    assert fake_providers.last_config["api_key"] == "per-call-cartesia-override"


# ---------------------------------------------------------------------------
# Backward compat: legacy global-only configs still resolve
# ---------------------------------------------------------------------------


def test_legacy_global_only_config_still_resolves(
    tmp_path, monkeypatch, fake_providers
):
    """A pre-v0.0.5 config with no projects: block keeps using the"""
    cfg = {
        "providers": {"openai": {"api_key": "legacy-key"}},
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

    llm.LLM("openai/gpt-4o-mini")
    assert fake_providers.last_config["api_key"] == "legacy-key"
