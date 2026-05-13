"""Tests for the inference factory key-resolution preflight."""

from __future__ import annotations

from typing import Any

import pytest
import yaml

from voicegateway.core.config import ConfigError
from voicegateway.inference import _factory, _llm, _project, _stt, _tts


class _LkShapedStub:
    """Provider-side stub carrying the LK surface the wrappers consume."""

    def __init__(self) -> None:
        from livekit.agents.stt import STTCapabilities
        from livekit.agents.tts import TTSCapabilities

        self.capabilities: Any = STTCapabilities(streaming=False, interim_results=False)
        self._tts_capabilities: Any = TTSCapabilities(streaming=False)
        self.sample_rate = 24000
        self.num_channels = 1

    def on(self, _event: str, _cb: Any) -> None:
        pass


class _FakeProvider:
    last_config: dict[str, Any] | None = None

    def __init__(self, config: dict[str, Any]) -> None:
        _FakeProvider.last_config = dict(config)

    def create_stt(self, model: str, **kwargs: Any) -> Any:
        return _LkShapedStub()

    def create_llm(self, model: str, **kwargs: Any) -> Any:
        return _LkShapedStub()

    def create_tts(self, model: str, voice: str | None = None, **kwargs: Any) -> Any:
        return _LkShapedStub()

    async def health_check(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    monkeypatch.delenv("VOICEGW_ACTIVE_PROJECT", raising=False)
    _project.reset_project()
    _FakeProvider.last_config = None
    yield
    _project.reset_project()


@pytest.fixture
def fake_create_provider(monkeypatch):
    def _create(_provider_name: str, config: dict[str, Any]) -> _FakeProvider:
        return _FakeProvider(config)

    monkeypatch.setattr(_stt, "create_provider", _create)
    monkeypatch.setattr(_llm, "create_provider", _create)
    monkeypatch.setattr(_tts, "create_provider", _create)
    return _create


def _build_gateway(tmp_path, monkeypatch, projects: dict | None = None):
    cfg = {
        "providers": {},
        "models": {"stt": {}, "llm": {}, "tts": {}},
        "stacks": {},
        "fallbacks": {"stt": [], "llm": [], "tts": []},
        "cost_tracking": {"enabled": False},
        "observability": {"latency_tracking": True},
    }
    if projects is not None:
        cfg["projects"] = projects
    cfg_path = tmp_path / "voicegw.yaml"
    cfg_path.write_text(yaml.dump(cfg))

    from voicegateway.core.gateway import Gateway

    gw = Gateway(config_path=str(cfg_path))
    monkeypatch.setattr(_factory, "_gateway", gw)
    return gw


# ---------------------------------------------------------------------------
# Cloud providers without keys raise a clear error
# ---------------------------------------------------------------------------


def test_stt_missing_key_raises_naming_provider_and_project(
    tmp_path, monkeypatch, fake_create_provider
):
    _build_gateway(tmp_path, monkeypatch)
    _project.set_project("mama-diner")

    with pytest.raises(ConfigError) as excinfo:
        _stt.STT("deepgram/nova-3")

    msg = str(excinfo.value)
    assert "deepgram" in msg
    assert "mama-diner" in msg
    # The error must guide the user to a fix path.
    assert "vg_add_provider" in msg or "voicegw.yaml" in msg


def test_llm_missing_key_raises_naming_provider_and_project(
    tmp_path, monkeypatch, fake_create_provider
):
    _build_gateway(tmp_path, monkeypatch)
    _project.set_project("tony-pizza")

    with pytest.raises(ConfigError) as excinfo:
        _llm.LLM("openai/gpt-4o-mini")

    msg = str(excinfo.value)
    assert "openai" in msg
    assert "tony-pizza" in msg


def test_tts_missing_key_raises_naming_provider_and_project(
    tmp_path, monkeypatch, fake_create_provider
):
    _build_gateway(tmp_path, monkeypatch)
    _project.set_project("voice-app")

    with pytest.raises(ConfigError) as excinfo:
        _tts.TTS("cartesia/sonic-3")

    msg = str(excinfo.value)
    assert "cartesia" in msg
    assert "voice-app" in msg


# ---------------------------------------------------------------------------
# Local providers skip the check
# ---------------------------------------------------------------------------


def test_local_whisper_stt_does_not_require_api_key(
    tmp_path, monkeypatch, fake_create_provider
):
    _build_gateway(tmp_path, monkeypatch)
    _stt.STT("whisper/large-v3")
    assert _FakeProvider.last_config is not None
    assert "api_key" not in _FakeProvider.last_config


def test_local_ollama_llm_does_not_require_api_key(
    tmp_path, monkeypatch, fake_create_provider
):
    _build_gateway(tmp_path, monkeypatch)
    _llm.LLM("ollama/qwen2.5:3b")
    assert _FakeProvider.last_config is not None
    assert "api_key" not in _FakeProvider.last_config


def test_local_kokoro_tts_does_not_require_api_key(
    tmp_path, monkeypatch, fake_create_provider
):
    _build_gateway(tmp_path, monkeypatch)
    _tts.TTS("kokoro/v019")
    assert _FakeProvider.last_config is not None
    assert "api_key" not in _FakeProvider.last_config


def test_local_piper_tts_does_not_require_api_key(
    tmp_path, monkeypatch, fake_create_provider
):
    _build_gateway(tmp_path, monkeypatch)
    _tts.TTS("piper/en_US-lessac-medium")
    assert _FakeProvider.last_config is not None


# ---------------------------------------------------------------------------
# Explicit api_key kwarg satisfies the preflight
# ---------------------------------------------------------------------------


def test_per_call_api_key_kwarg_satisfies_preflight(
    tmp_path, monkeypatch, fake_create_provider
):
    _build_gateway(tmp_path, monkeypatch)
    _stt.STT("deepgram/nova-3", api_key="explicit-override")
    assert _FakeProvider.last_config is not None
    assert _FakeProvider.last_config["api_key"] == "explicit-override"


# ---------------------------------------------------------------------------
# Per-project key satisfies the preflight; missing one does not
# ---------------------------------------------------------------------------


def test_per_project_yaml_key_satisfies_preflight(
    tmp_path, monkeypatch, fake_create_provider
):
    _build_gateway(
        tmp_path,
        monkeypatch,
        projects={
            "tony-pizza": {
                "name": "Tony",
                "providers": {"deepgram": {"api_key": "tony-dg-key"}},
            }
        },
    )
    _project.set_project("tony-pizza")
    _stt.STT("deepgram/nova-3")
    assert _FakeProvider.last_config["api_key"] == "tony-dg-key"


def test_other_project_in_yaml_does_not_satisfy_preflight(
    tmp_path, monkeypatch, fake_create_provider
):
    """A key configured under project A must NOT satisfy a request"""
    _build_gateway(
        tmp_path,
        monkeypatch,
        projects={
            "tony-pizza": {
                "name": "Tony",
                "providers": {"deepgram": {"api_key": "tony-dg-key"}},
            },
            "mama-diner": {"name": "Mama"},
        },
    )
    _project.set_project("mama-diner")

    with pytest.raises(ConfigError) as excinfo:
        _stt.STT("deepgram/nova-3")

    msg = str(excinfo.value)
    assert "mama-diner" in msg
    assert "tony" not in msg.lower()  # Don't mention the unrelated project.


# ---------------------------------------------------------------------------
# Empty api_key string is treated as "not configured"
# ---------------------------------------------------------------------------


def test_empty_api_key_treated_as_missing(tmp_path, monkeypatch, fake_create_provider):
    """A YAML entry with ``api_key: ''`` (e.g. an unset env var that"""
    _build_gateway(
        tmp_path,
        monkeypatch,
        projects={
            "tony-pizza": {
                "name": "Tony",
                "providers": {"deepgram": {"api_key": ""}},
            }
        },
    )
    _project.set_project("tony-pizza")

    with pytest.raises(ConfigError):
        _stt.STT("deepgram/nova-3")
