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


# --- Schema validation tests ---

import yaml


def test_unknown_top_level_key_raises_error(tmp_path):
    path = tmp_path / "bad.yaml"
    with open(path, "w") as f:
        yaml.dump({"providrs": {"openai": {"api_key": "test"}}}, f)
    with pytest.raises(ConfigError, match="providrs"):
        GatewayConfig.load(str(path))


def test_unknown_top_level_key_suggests_correction(tmp_path):
    path = tmp_path / "bad.yaml"
    with open(path, "w") as f:
        yaml.dump({"providrs": {}}, f)
    with pytest.raises(ConfigError, match="did you mean"):
        GatewayConfig.load(str(path))


def test_negative_daily_budget_raises_error(tmp_path):
    path = tmp_path / "bad.yaml"
    cfg = {
        "providers": {},
        "models": {"stt": {}},
        "projects": {"test": {"name": "T", "daily_budget": -5}},
    }
    with open(path, "w") as f:
        yaml.dump(cfg, f)
    with pytest.raises(ConfigError, match="daily_budget"):
        GatewayConfig.load(str(path))


def test_invalid_budget_action_raises_error(tmp_path):
    path = tmp_path / "bad.yaml"
    cfg = {
        "providers": {},
        "models": {"stt": {}},
        "projects": {"test": {"name": "T", "budget_action": "explode"}},
    }
    with open(path, "w") as f:
        yaml.dump(cfg, f)
    with pytest.raises(ConfigError, match="budget_action"):
        GatewayConfig.load(str(path))


def test_observability_config_loaded(example_config_path):
    config = GatewayConfig.load(example_config_path)
    assert config.observability.get("latency_tracking") is True
