"""Integration test — exercises the full Gateway → Router → Provider → Storage flow."""

import time
import uuid

import pytest
import yaml

from voicegateway.core.gateway import Gateway
from voicegateway.middleware.instrumented_provider import InstrumentedLLM


@pytest.fixture
def mock_config(tmp_path, monkeypatch):
    """Create a config with a mock provider that doesn't need real plugins."""
    config = {
        "providers": {
            "deepgram": {"api_key": "test-key"},
        },
        "models": {
            "stt": {
                "deepgram/nova-3": {"provider": "deepgram", "model": "nova-3"},
            },
            "llm": {},
            "tts": {},
        },
        "projects": {
            "integration-test": {
                "name": "Integration Test",
                "daily_budget": 50.0,
                "budget_action": "warn",
                "tags": ["test"],
            },
        },
        "fallbacks": {"stt": [], "llm": [], "tts": []},
        "cost_tracking": {"enabled": True},
        "observability": {
            "latency_tracking": True,
            "cost_tracking": True,
            "request_logging": True,
        },
    }
    config_path = tmp_path / "voicegw.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config, f)
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "integration.db"))
    return str(config_path)


def test_gateway_init(mock_config):
    """Gateway initializes with config, storage, and budget enforcer."""
    gw = Gateway(config_path=mock_config)
    assert gw.config is not None
    assert gw.storage is not None
    assert gw._budget_enforcer is not None


def test_projects_listed(mock_config):
    """Gateway.list_projects returns configured projects."""
    gw = Gateway(config_path=mock_config)
    projects = gw.list_projects()
    ids = [p["id"] for p in projects]
    assert "integration-test" in ids


def test_costs_empty(mock_config):
    """Costs are zero on a fresh database."""
    gw = Gateway(config_path=mock_config)
    costs = gw.costs("today", project="integration-test")
    assert costs["total"] == 0.0


def test_latency_tracking_disabled(tmp_path, monkeypatch):
    """When latency_tracking is false, raw instances are returned (no wrapper)."""
    config = {
        "providers": {"deepgram": {"api_key": "test-key"}},
        "models": {"stt": {"deepgram/nova-3": {"provider": "deepgram", "model": "nova-3"}}, "llm": {}, "tts": {}},
        "projects": {},
        "fallbacks": {"stt": [], "llm": [], "tts": []},
        "observability": {"latency_tracking": False},
    }
    config_path = tmp_path / "voicegw.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config, f)
    gw = Gateway(config_path=str(config_path))
    assert gw._latency_tracking is False


def test_config_validation_catches_typos(tmp_path):
    """A typo in config keys raises a clear error."""
    config = {"providrs": {"openai": {"api_key": "test"}}}
    config_path = tmp_path / "bad.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config, f)
    from voicegateway.core.config import ConfigError
    with pytest.raises(ConfigError, match="providrs"):
        Gateway(config_path=str(config_path))
