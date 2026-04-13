"""Tests for config loading and env var substitution."""

import os
import pytest

from voicegateway.core.config import GatewayConfig, ConfigError


def test_load_example_config(example_config_path):
    config = GatewayConfig.load(example_config_path)
    assert "deepgram" in config.providers
    assert "openai" in config.providers


def test_env_var_substitution(example_config_path, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "my-secret-key")
    config = GatewayConfig.load(example_config_path)
    assert config.providers["deepgram"]["api_key"] == "my-secret-key"


def test_missing_config_file():
    with pytest.raises(ConfigError):
        GatewayConfig.load("/nonexistent/path/voicegw.yaml")


def test_get_model_config(example_config_path):
    config = GatewayConfig.load(example_config_path)
    assert config.get_model_config("stt", "deepgram/nova-3") is not None
    assert config.get_model_config("llm", "openai/gpt-4o-mini") is not None
    assert config.get_model_config("tts", "cartesia/sonic-3") is not None
    assert config.get_model_config("stt", "nonexistent/model") is None


def test_fallbacks_loaded(example_config_path):
    config = GatewayConfig.load(example_config_path)
    assert "stt" in config.fallbacks
    assert "llm" in config.fallbacks
    assert "tts" in config.fallbacks
    assert len(config.fallbacks["stt"]) >= 2
