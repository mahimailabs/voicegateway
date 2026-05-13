"""Tests for the v0.0.5 per-project provider keys schema."""

from __future__ import annotations

import yaml

from voicegateway.core.config import GatewayConfig


def _write_config(path, content: dict) -> str:
    config_path = path / "voicegw.yaml"
    config_path.write_text(yaml.dump(content))
    return str(config_path)


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


def test_legacy_global_providers_still_load(tmp_path):
    """Configs without project.providers continue to work — the"""
    cfg_path = _write_config(
        tmp_path,
        {
            "providers": {
                "openai": {"api_key": "global-openai-key"},
                "deepgram": {"api_key": "global-dg-key"},
            },
            "models": {"stt": {}, "llm": {}, "tts": {}},
            "stacks": {},
            "fallbacks": {"stt": [], "llm": [], "tts": []},
            "cost_tracking": {"enabled": False},
            "observability": {"latency_tracking": True},
        },
    )
    cfg = GatewayConfig.load(cfg_path)
    assert cfg.providers["openai"]["api_key"] == "global-openai-key"
    assert cfg.default_project is None
    assert cfg.projects == {}


def test_get_provider_config_falls_back_to_global(tmp_path):
    cfg_path = _write_config(
        tmp_path,
        {
            "providers": {"openai": {"api_key": "global-openai-key"}},
            "models": {"stt": {}, "llm": {}, "tts": {}},
            "stacks": {},
            "fallbacks": {"stt": [], "llm": [], "tts": []},
            "cost_tracking": {"enabled": False},
            "observability": {"latency_tracking": True},
        },
    )
    cfg = GatewayConfig.load(cfg_path)

    # No project at all → still resolves via top-level providers.
    resolved = cfg.get_provider_config_for_project("openai", project_id=None)
    assert resolved == {"api_key": "global-openai-key"}


# ---------------------------------------------------------------------------
# Per-project provider keys
# ---------------------------------------------------------------------------


def test_project_providers_parsed_from_yaml(tmp_path):
    cfg_path = _write_config(
        tmp_path,
        {
            "providers": {"openai": {"api_key": "global-openai-key"}},
            "projects": {
                "tony-pizza": {
                    "name": "Tony's Pizza",
                    "providers": {
                        "openai": {"api_key": "tony-openai-key"},
                        "deepgram": {"api_key": "tony-dg-key"},
                    },
                },
            },
            "models": {"stt": {}, "llm": {}, "tts": {}},
            "stacks": {},
            "fallbacks": {"stt": [], "llm": [], "tts": []},
            "cost_tracking": {"enabled": False},
            "observability": {"latency_tracking": True},
        },
    )
    cfg = GatewayConfig.load(cfg_path)
    project = cfg.projects["tony-pizza"]
    assert project.providers["openai"]["api_key"] == "tony-openai-key"
    assert project.providers["deepgram"]["api_key"] == "tony-dg-key"


def test_project_provider_overrides_global(tmp_path):
    cfg_path = _write_config(
        tmp_path,
        {
            "providers": {"openai": {"api_key": "global-openai-key"}},
            "projects": {
                "tony-pizza": {
                    "name": "Tony's Pizza",
                    "providers": {"openai": {"api_key": "tony-openai-key"}},
                },
            },
            "models": {"stt": {}, "llm": {}, "tts": {}},
            "stacks": {},
            "fallbacks": {"stt": [], "llm": [], "tts": []},
            "cost_tracking": {"enabled": False},
            "observability": {"latency_tracking": True},
        },
    )
    cfg = GatewayConfig.load(cfg_path)

    # Project-level entry wins.
    resolved = cfg.get_provider_config_for_project("openai", project_id="tony-pizza")
    assert resolved["api_key"] == "tony-openai-key"


def test_project_without_provider_falls_back_to_global(tmp_path):
    """Project has providers but not the requested one → falls back to"""
    cfg_path = _write_config(
        tmp_path,
        {
            "providers": {
                "openai": {"api_key": "global-openai-key"},
                "deepgram": {"api_key": "global-dg-key"},
            },
            "projects": {
                "mama-diner": {
                    "name": "Mama Diner",
                    "providers": {"openai": {"api_key": "mama-openai-key"}},
                },
            },
            "models": {"stt": {}, "llm": {}, "tts": {}},
            "stacks": {},
            "fallbacks": {"stt": [], "llm": [], "tts": []},
            "cost_tracking": {"enabled": False},
            "observability": {"latency_tracking": True},
        },
    )
    cfg = GatewayConfig.load(cfg_path)
    deepgram_for_mama = cfg.get_provider_config_for_project(
        "deepgram", project_id="mama-diner"
    )
    assert deepgram_for_mama["api_key"] == "global-dg-key"

    openai_for_mama = cfg.get_provider_config_for_project(
        "openai", project_id="mama-diner"
    )
    assert openai_for_mama["api_key"] == "mama-openai-key"


def test_unknown_project_falls_back_to_global(tmp_path):
    """A project_id that isn't in voicegw.yaml resolves via global."""
    cfg_path = _write_config(
        tmp_path,
        {
            "providers": {"openai": {"api_key": "global-openai-key"}},
            "projects": {},
            "models": {"stt": {}, "llm": {}, "tts": {}},
            "stacks": {},
            "fallbacks": {"stt": [], "llm": [], "tts": []},
            "cost_tracking": {"enabled": False},
            "observability": {"latency_tracking": True},
        },
    )
    cfg = GatewayConfig.load(cfg_path)
    resolved = cfg.get_provider_config_for_project("openai", project_id="not-a-project")
    assert resolved["api_key"] == "global-openai-key"


def test_provider_returns_empty_when_neither_global_nor_project(tmp_path):
    cfg_path = _write_config(
        tmp_path,
        {
            "providers": {},
            "projects": {"tony-pizza": {"name": "Tony", "providers": {}}},
            "models": {"stt": {}, "llm": {}, "tts": {}},
            "stacks": {},
            "fallbacks": {"stt": [], "llm": [], "tts": []},
            "cost_tracking": {"enabled": False},
            "observability": {"latency_tracking": True},
        },
    )
    cfg = GatewayConfig.load(cfg_path)
    resolved = cfg.get_provider_config_for_project("openai", project_id="tony-pizza")
    assert resolved == {}


# ---------------------------------------------------------------------------
# default_project field
# ---------------------------------------------------------------------------


def test_default_project_field_parsed(tmp_path):
    cfg_path = _write_config(
        tmp_path,
        {
            "providers": {"openai": {"api_key": "k"}},
            "projects": {"tony-pizza": {"name": "Tony"}},
            "default_project": "tony-pizza",
            "models": {"stt": {}, "llm": {}, "tts": {}},
            "stacks": {},
            "fallbacks": {"stt": [], "llm": [], "tts": []},
            "cost_tracking": {"enabled": False},
            "observability": {"latency_tracking": True},
        },
    )
    cfg = GatewayConfig.load(cfg_path)
    assert cfg.default_project == "tony-pizza"


def test_default_project_defaults_to_none(tmp_path):
    cfg_path = _write_config(
        tmp_path,
        {
            "providers": {},
            "models": {"stt": {}, "llm": {}, "tts": {}},
            "stacks": {},
            "fallbacks": {"stt": [], "llm": [], "tts": []},
            "cost_tracking": {"enabled": False},
            "observability": {"latency_tracking": True},
        },
    )
    cfg = GatewayConfig.load(cfg_path)
    assert cfg.default_project is None


# ---------------------------------------------------------------------------
# Backward compat with the existing legacy configs the repo carries
# ---------------------------------------------------------------------------


def test_existing_example_config_loads(example_config_path):
    """The shipped voicegw.example.yaml must continue to load with the"""
    cfg = GatewayConfig.load(example_config_path)
    # Just verify load completes and core attributes exist.
    assert isinstance(cfg.providers, dict)
    assert isinstance(cfg.projects, dict)
