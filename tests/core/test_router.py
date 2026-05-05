"""Tests for the model router."""

import pytest

from voicegateway import Gateway
from voicegateway.core.router import ModelNotFoundError


def _has_plugin(name):
    try:
        __import__(f"livekit.plugins.{name}")
        return True
    except (ImportError, ModuleNotFoundError):
        return False


def test_gateway_init(example_config_path):
    gw = Gateway(config_path=example_config_path)
    assert gw.config is not None


@pytest.mark.skipif(not _has_plugin("deepgram"), reason="deepgram plugin not installed")
def test_stt_resolves(example_config_path):
    gw = Gateway(config_path=example_config_path)
    stt = gw.stt("deepgram/nova-3")
    assert stt is not None


@pytest.mark.skipif(not _has_plugin("openai"), reason="openai plugin not installed")
def test_llm_resolves(example_config_path):
    gw = Gateway(config_path=example_config_path)
    llm = gw.llm("openai/gpt-4o-mini")
    assert llm is not None


@pytest.mark.skipif(not _has_plugin("cartesia"), reason="cartesia plugin not installed")
def test_tts_resolves(example_config_path):
    gw = Gateway(config_path=example_config_path)
    tts = gw.tts("cartesia/sonic-3")
    assert tts is not None


@pytest.mark.skipif(not _has_plugin("openai"), reason="openai plugin not installed")
def test_ollama_llm(example_config_path):
    gw = Gateway(config_path=example_config_path)
    llm = gw.llm("ollama/qwen2.5:3b")
    assert llm is not None


def test_model_not_found(example_config_path):
    gw = Gateway(config_path=example_config_path)
    with pytest.raises(ModelNotFoundError):
        gw.llm("totally/fake-model")


def test_status(example_config_path):
    gw = Gateway(config_path=example_config_path)
    status = gw.status()
    assert isinstance(status, dict)


def test_resolve_unknown_modality_raises_value_error(example_config_path):
    """An unsupported modality string raises ValueError after passing config lookup."""
    from unittest.mock import MagicMock

    from voicegateway.core.router import Router

    gw = Gateway(config_path=example_config_path)
    router = Router(gw.config)
    # Stub config + provider lookups so the test reaches the modality
    # dispatch without depending on yaml entries or installed plugins.
    router._config.get_model_config = MagicMock(  # type: ignore[method-assign]
        return_value={"provider": "openai", "model": "fake"}
    )
    router._get_provider = MagicMock(return_value=MagicMock())  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="Unknown modality"):
        router.resolve("openai/fake", "embedding")


def test_get_provider_status_empty_before_resolve(example_config_path):
    """`get_provider_status` is empty before any provider has been resolved."""
    from voicegateway.core.router import Router

    gw = Gateway(config_path=example_config_path)
    router = Router(gw.config)
    assert router.get_provider_status() == {}


def test_get_provider_status_reflects_initialized_providers(example_config_path):
    """`get_provider_status` lists providers added to the cache."""
    from unittest.mock import MagicMock

    from voicegateway.core.router import Router

    gw = Gateway(config_path=example_config_path)
    router = Router(gw.config)
    # Manually populate the cache to simulate a prior resolve, since
    # plugins are usually not installed in the test environment.
    router._providers["fake-provider"] = MagicMock()
    status = router.get_provider_status()
    assert status == {"fake-provider": {"initialized": True}}
