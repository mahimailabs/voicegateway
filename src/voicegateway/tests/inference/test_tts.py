"""Tests for voicegateway.inference.tts.TTS."""

from __future__ import annotations

import contextvars
import warnings
from typing import Any

import pytest
import yaml

from voicegateway.inference import factory, tts
from voicegateway.inference.resolution import ModelResolutionError
from voicegateway.inference.session.context import get_session_id

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeTTS:
    """Pretends to be a livekit.plugins.<provider>.TTS instance."""

    def __init__(self, model: str, voice: str | None = None, **kwargs: Any) -> None:
        from livekit.agents.tts import TTSCapabilities

        self.capabilities = TTSCapabilities(streaming=False)
        self.sample_rate = 24000
        self.num_channels = 1
        self.model = model
        self.voice = voice
        self.kwargs = kwargs

    def on(self, event: str, callback: Any) -> None:
        # No-op for tests; the bridge in InstrumentedTTS registers a
        # listener but the fake never fires events.
        pass

    async def aclose(self) -> None:
        pass


class _FakeProvider:
    """A BaseProvider-like fake whose create_tts records the call."""

    last_config: dict[str, Any] | None = None
    last_create_tts: dict[str, Any] | None = None
    last_provider_name: str | None = None

    def __init__(self, config: dict[str, Any]) -> None:
        _FakeProvider.last_config = dict(config)
        self._config = config

    def create_stt(self, model: str, **kwargs: Any) -> Any:
        raise NotImplementedError

    def create_llm(self, model: str, **kwargs: Any) -> Any:
        raise NotImplementedError

    def create_tts(
        self, model: str, voice: str | None = None, **kwargs: Any
    ) -> _FakeTTS:
        _FakeProvider.last_create_tts = {
            "model": model,
            "voice": voice,
            **kwargs,
        }
        return _FakeTTS(model, voice=voice, **kwargs)

    async def health_check(self) -> bool:
        return True


@pytest.fixture
def fake_provider(monkeypatch):
    _FakeProvider.last_config = None
    _FakeProvider.last_create_tts = None
    _FakeProvider.last_provider_name = None

    def _create(provider_name: str, config: dict[str, Any]) -> _FakeProvider:
        _FakeProvider.last_provider_name = provider_name
        return _FakeProvider(config)

    monkeypatch.setattr(tts, "create_provider", _create)
    return _FakeProvider


@pytest.fixture
def configured_gateway(tmp_path, monkeypatch):
    cfg = {
        "providers": {
            "cartesia": {"api_key": "from-yaml-cartesia-key"},
            "elevenlabs": {"api_key": "from-yaml-elevenlabs-key"},
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
        result = tts.TTS("cartesia/sonic-3")
        assert result.__class__.__name__ == "InstrumentedTTS"
        assert result._wrapped.model == "sonic-3"

    def test_all_lk_params_accepted(self, configured_gateway, fake_provider):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = tts.TTS(
                "cartesia/sonic-3",
                voice="bob",
                language="en",
                encoding="pcm_s16le",
                sample_rate=24000,
                base_url="https://example.test",
                api_key="override-key",
                http_session=None,
                extra_kwargs={"speed": 1.1},
            )
        assert result.__class__.__name__ == "InstrumentedTTS"


class TestVoiceSuffixParsing:
    def test_colon_suffix_treated_as_voice(self, configured_gateway, fake_provider):
        # Mirrors LK TTS: "cartesia/sonic-3:my-voice-id" puts "my-voice-id"
        # into the voice slot, not language.
        tts.TTS("cartesia/sonic-3:my-voice-id")
        assert fake_provider.last_create_tts["model"] == "sonic-3"
        assert fake_provider.last_create_tts["voice"] == "my-voice-id"

    def test_explicit_voice_wins_over_suffix(self, configured_gateway, fake_provider):
        tts.TTS("cartesia/sonic-3:from-suffix", voice="explicit-voice")
        assert fake_provider.last_create_tts["voice"] == "explicit-voice"

    def test_no_colon_no_voice(self, configured_gateway, fake_provider):
        tts.TTS("cartesia/sonic-3")
        assert fake_provider.last_create_tts["voice"] is None

    def test_suffix_does_not_affect_language(self, configured_gateway, fake_provider):
        # Critical asymmetry vs STT: `:my-voice-id` must NOT land as
        # `language="my-voice-id"`. Language stays None.
        tts.TTS("cartesia/sonic-3:my-voice-id")
        assert "language" not in fake_provider.last_create_tts


class TestModelResolution:
    def test_missing_model_raises(self, configured_gateway, fake_provider):
        with pytest.raises(TypeError):
            tts.TTS()  # type: ignore[call-arg]

    def test_empty_model_raises(self, configured_gateway, fake_provider):
        with pytest.raises(ValueError, match="provider/model"):
            tts.TTS("")

    def test_unknown_provider_raises(self, configured_gateway, fake_provider):
        with pytest.raises(ModelResolutionError, match="Unknown provider"):
            tts.TTS("not-a-real-co/sonic-3")


class TestKwargForwarding:
    def test_extra_kwargs_spread_to_create_tts(self, configured_gateway, fake_provider):
        tts.TTS(
            "cartesia/sonic-3",
            extra_kwargs={"speed": 1.2, "stability": 0.5},
        )
        assert fake_provider.last_create_tts["speed"] == 1.2
        assert fake_provider.last_create_tts["stability"] == 0.5

    def test_language_and_sample_rate_forwarded(
        self, configured_gateway, fake_provider
    ):
        tts.TTS("cartesia/sonic-3", language="en", sample_rate=22050)
        assert fake_provider.last_create_tts["language"] == "en"
        assert fake_provider.last_create_tts["sample_rate"] == 22050

    def test_omitted_params_not_forwarded(self, configured_gateway, fake_provider):
        tts.TTS("cartesia/sonic-3")
        # Only `model` and `voice` (None) should appear; NOT_GIVEN
        # sentinels must NOT leak as explicit kwargs.
        assert set(fake_provider.last_create_tts) == {"model", "voice"}

    def test_voice_forwarded_when_given(self, configured_gateway, fake_provider):
        tts.TTS("cartesia/sonic-3", voice="alice")
        assert fake_provider.last_create_tts["voice"] == "alice"


class TestApiKeyOverride:
    def test_override_replaces_yaml_key(self, configured_gateway, fake_provider):
        tts.TTS("cartesia/sonic-3", api_key="per-call-override")
        assert fake_provider.last_config["api_key"] == "per-call-override"

    def test_no_override_uses_yaml_key(self, configured_gateway, fake_provider):
        tts.TTS("cartesia/sonic-3")
        assert fake_provider.last_config["api_key"] == "from-yaml-cartesia-key"


class TestSessionCorrelation:
    def test_construction_creates_session_id(self, configured_gateway, fake_provider):
        def _scenario():
            assert get_session_id() is None
            tts.TTS("cartesia/sonic-3")
            return get_session_id()

        sid = contextvars.copy_context().run(_scenario)
        assert sid is not None
        assert sid.startswith("vg-")

    def test_two_constructions_share_session_id(
        self, configured_gateway, fake_provider
    ):
        def _scenario():
            tts.TTS("cartesia/sonic-3")
            first = get_session_id()
            tts.TTS("cartesia/sonic-3")
            second = get_session_id()
            return first, second

        a, b = contextvars.copy_context().run(_scenario)
        assert a is not None
        assert a == b


class TestUnsupportedParamRejected:
    def test_api_secret_no_longer_a_kwarg(self, configured_gateway, fake_provider):
        with pytest.raises(TypeError):
            tts.TTS("cartesia/sonic-3", api_secret="ignored")

    def test_fallback_no_longer_a_kwarg(self, configured_gateway, fake_provider):
        with pytest.raises(TypeError):
            tts.TTS("cartesia/sonic-3", fallback="elevenlabs/eleven-turbo-v2")

    def test_conn_options_no_longer_a_kwarg(self, configured_gateway, fake_provider):
        with pytest.raises(TypeError):
            tts.TTS("cartesia/sonic-3", conn_options="anything")
