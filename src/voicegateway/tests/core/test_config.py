"""Tests for config loading and env var substitution."""

import pytest
import yaml

from voicegateway.core.config import ConfigError, GatewayConfig


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


def test_unknown_top_level_key_raises_error_with_suggestion(tmp_path):
    path = tmp_path / "bad.yaml"
    with open(path, "w") as f:
        yaml.dump({"providrs": {"openai": {"api_key": "test"}}}, f)
    with pytest.raises(
        ConfigError, match="providrs.*did you mean|did you mean.*providrs"
    ):
        GatewayConfig.load(str(path))


def test_livekit_block_is_accepted(tmp_path):
    """A ``livekit:`` block must load, not trip the strict top-level schema.

    The dashboard Diagnostics page tells operators to add this block, and the
    diagnostics resolver reads it, so ``voicegw serve`` must accept it instead
    of crashing with "Extra inputs are not permitted".
    """
    path = tmp_path / "voicegw.yaml"
    with open(path, "w") as f:
        yaml.dump({"livekit": {"url": "wss://x", "api_key": "k", "api_secret": "s"}}, f)
    # Must not raise ConfigError.
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


def test_section_overrides_reach_gateway_config(tmp_path):
    """retention, workers and ingest blocks are parsed, not silently dropped."""
    path = tmp_path / "voicegw.yaml"
    path.write_text(
        yaml.dump(
            {
                "retention": {"default_days": 30},
                "workers": {"enabled": False, "rollup_interval_seconds": 111},
                "ingest": {
                    "requests_per_minute": 30,
                    "burst": 60,
                    "max_batch_size": 100,
                },
            }
        )
    )
    cfg = GatewayConfig.load(str(path))
    assert cfg.retention.default_days == 30
    assert cfg.workers.enabled is False
    assert cfg.workers.rollup_interval_seconds == 111
    assert cfg.ingest.requests_per_minute == 30
    assert cfg.ingest.burst == 60
    assert cfg.ingest.max_batch_size == 100
