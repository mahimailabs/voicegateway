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
