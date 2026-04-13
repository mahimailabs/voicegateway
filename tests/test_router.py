"""Tests for the model router."""

import pytest

from voicegateway import Gateway
from voicegateway.core.router import ModelNotFoundError


def test_gateway_init(example_config_path):
    gw = Gateway(config_path=example_config_path)
    assert gw.config is not None


def test_stt_resolves(example_config_path):
    gw = Gateway(config_path=example_config_path)
    stt = gw.stt("deepgram/nova-3")
    assert stt is not None
    assert type(stt).__name__ == "STT"


def test_llm_resolves(example_config_path):
    gw = Gateway(config_path=example_config_path)
    llm = gw.llm("openai/gpt-4o-mini")
    assert llm is not None
    assert type(llm).__name__ == "LLM"


def test_tts_resolves(example_config_path):
    gw = Gateway(config_path=example_config_path)
    tts = gw.tts("cartesia/sonic-3")
    assert tts is not None


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
