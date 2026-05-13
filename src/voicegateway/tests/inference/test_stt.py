"""Tests for voicegateway.inference.stt.STT."""

from __future__ import annotations

import contextvars
import warnings
from typing import Any

import pytest
import yaml
from livekit.agents.types import NOT_GIVEN

from voicegateway.inference import factory, stt
from voicegateway.inference.resolution import ModelResolutionError
from voicegateway.inference.session.context import get_session_id

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeSTT:
    """Pretends to be a livekit.plugins.<provider>.STT instance."""

    def __init__(self, model: str, **kwargs: Any) -> None:
        from livekit.agents.stt import STTCapabilities

        self.capabilities = STTCapabilities(streaming=False, interim_results=False)
        self.model = model
        self.kwargs = kwargs

    def on(self, event: str, callback: Any) -> None:
        # No-op for tests; the bridge in InstrumentedSTT registers a
        # listener but the fake never fires events.
        pass

    def aclose(self) -> None:
        pass


class _FakeProvider:
    """A BaseProvider-like fake whose create_stt records the call."""

    last_config: dict[str, Any] | None = None
    last_create_stt: dict[str, Any] | None = None

    def __init__(self, config: dict[str, Any]) -> None:
        _FakeProvider.last_config = dict(config)
        self._config = config

    def create_stt(self, model: str, **kwargs: Any) -> _FakeSTT:
        _FakeProvider.last_create_stt = {"model": model, **kwargs}
        return _FakeSTT(model, **kwargs)

    def create_llm(self, model: str, **kwargs: Any) -> Any:
        raise NotImplementedError

    def create_tts(self, model: str, voice: str | None = None, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def health_check(self) -> bool:
        return True


@pytest.fixture
def fake_provider(monkeypatch):
    """Replace stt.create_provider with one that returns _FakeProvider."""
    _FakeProvider.last_config = None
    _FakeProvider.last_create_stt = None

    def _create(provider_name: str, config: dict[str, Any]) -> _FakeProvider:
        return _FakeProvider(config)

    monkeypatch.setattr(stt, "create_provider", _create)
    return _FakeProvider


@pytest.fixture
def configured_gateway(tmp_path, monkeypatch):
    """Build a Gateway from a minimal YAML and inject as the cached singleton."""
    cfg = {
        "providers": {
            "deepgram": {"api_key": "from-yaml-deepgram-key"},
            "openai": {"api_key": "from-yaml-openai-key"},
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
        result = stt.STT("deepgram/nova-3")
        # InstrumentedSTT is what wrap_provider returns; the underlying
        # _wrapped is the _FakeSTT we stubbed.
        assert result.__class__.__name__ == "InstrumentedSTT"
        assert result._wrapped.model == "nova-3"

    def test_all_lk_params_accepted(self, configured_gateway, fake_provider):
        # The drop-in claim: every LK 1.5.7 inference.STT param is accepted
        # without raising on construction. Suppress the warn-and-ignore
        # warnings for parameters not yet honored.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = stt.STT(
                "deepgram/nova-3",
                language="en",
                base_url="https://example.test",
                encoding="pcm_s16le",
                sample_rate=16000,
                api_key="override-key",
                api_secret="ignored",
                http_session=None,
                extra_kwargs={"keyterm": ["livekit"]},
                fallback="cartesia/ink-whisper",
                conn_options=None,
            )
        assert result.__class__.__name__ == "InstrumentedSTT"


class TestModelResolution:
    def test_language_suffix_extracted(self, configured_gateway, fake_provider):
        stt.STT("deepgram/nova-3:en")
        assert fake_provider.last_create_stt["language"] == "en"
        assert fake_provider.last_create_stt["model"] == "nova-3"

    def test_explicit_language_wins_over_suffix(
        self, configured_gateway, fake_provider
    ):
        stt.STT("deepgram/nova-3:en", language="de")
        assert fake_provider.last_create_stt["language"] == "de"

    def test_no_colon_no_language(self, configured_gateway, fake_provider):
        stt.STT("deepgram/nova-3")
        assert "language" not in fake_provider.last_create_stt

    def test_missing_model_raises(self, configured_gateway, fake_provider):
        with pytest.raises(ValueError, match="provider/model"):
            stt.STT()

    def test_not_given_model_raises(self, configured_gateway, fake_provider):
        with pytest.raises(ValueError, match="provider/model"):
            stt.STT(NOT_GIVEN)

    def test_unknown_provider_raises(self, configured_gateway, fake_provider):
        with pytest.raises(ModelResolutionError, match="Unknown provider"):
            stt.STT("not-a-real-co/whisper")


class TestKwargForwarding:
    def test_extra_kwargs_spread_to_create_stt(self, configured_gateway, fake_provider):
        stt.STT(
            "deepgram/nova-3",
            extra_kwargs={"keyterm": ["livekit", "agent"], "smart_format": True},
        )
        assert fake_provider.last_create_stt["keyterm"] == ["livekit", "agent"]
        assert fake_provider.last_create_stt["smart_format"] is True

    def test_encoding_and_sample_rate_forwarded(
        self, configured_gateway, fake_provider
    ):
        stt.STT("deepgram/nova-3", encoding="pcm_s16le", sample_rate=24000)
        assert fake_provider.last_create_stt["encoding"] == "pcm_s16le"
        assert fake_provider.last_create_stt["sample_rate"] == 24000

    def test_omitted_params_not_forwarded(self, configured_gateway, fake_provider):
        stt.STT("deepgram/nova-3")
        # Only `model` should appear; nothing else should be passed through
        # implicitly (NOT_GIVEN sentinels must NOT appear as kwargs).
        assert set(fake_provider.last_create_stt) == {"model"}


class TestApiKeyOverride:
    def test_override_replaces_yaml_key(self, configured_gateway, fake_provider):
        stt.STT("deepgram/nova-3", api_key="per-call-override")
        # The fake provider's __init__ recorded the config dict; the
        # api_key field should be the per-call override, not the YAML one.
        assert fake_provider.last_config["api_key"] == "per-call-override"

    def test_no_override_uses_yaml_key(self, configured_gateway, fake_provider):
        stt.STT("deepgram/nova-3")
        assert fake_provider.last_config["api_key"] == "from-yaml-deepgram-key"


class TestSessionCorrelation:
    def test_construction_creates_session_id(self, configured_gateway, fake_provider):
        # Run inside a copied context so the autouse reset_session_id
        # in the conftest doesn't fight the assertion.
        def _scenario():
            assert get_session_id() is None
            stt.STT("deepgram/nova-3")
            return get_session_id()

        sid = contextvars.copy_context().run(_scenario)
        assert sid is not None
        assert sid.startswith("vg-")

    def test_two_constructions_share_session_id(
        self, configured_gateway, fake_provider
    ):
        def _scenario():
            stt.STT("deepgram/nova-3")
            first = get_session_id()
            stt.STT("deepgram/nova-3")
            second = get_session_id()
            return first, second

        a, b = contextvars.copy_context().run(_scenario)
        assert a is not None
        assert a == b


class TestUnsupportedParamWarnings:
    def test_api_secret_warns(self, configured_gateway, fake_provider):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            stt.STT("deepgram/nova-3", api_secret="ignored")
        messages = [str(w.message) for w in caught]
        assert any("api_secret" in m for m in messages)

    def test_fallback_warns(self, configured_gateway, fake_provider):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            stt.STT("deepgram/nova-3", fallback="cartesia/ink-whisper")
        messages = [str(w.message) for w in caught]
        assert any("fallback" in m for m in messages)

    def test_conn_options_warns(self, configured_gateway, fake_provider):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            stt.STT("deepgram/nova-3", conn_options="anything")
        messages = [str(w.message) for w in caught]
        assert any("conn_options" in m for m in messages)
