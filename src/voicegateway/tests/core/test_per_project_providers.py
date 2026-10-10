"""Per-project provider keys: project entries win, everything else falls back."""

from __future__ import annotations

import pytest
import yaml

from voicegateway.core.config import GatewayConfig

_BASE = {
    "models": {"stt": {}, "llm": {}, "tts": {}},
    "fallbacks": {"stt": [], "llm": [], "tts": []},
    "cost_tracking": {"enabled": False},
}


def _load(tmp_path, **content) -> GatewayConfig:
    path = tmp_path / "voicegw.yaml"
    path.write_text(yaml.dump({**_BASE, **content}))
    return GatewayConfig.load(str(path))


@pytest.mark.parametrize(
    ("provider", "project_id", "expected_key"),
    [
        ("openai", None, "global-openai-key"),  # no project at all
        ("openai", "mama-diner", "mama-openai-key"),  # project entry wins
        ("deepgram", "mama-diner", "global-dg-key"),  # project lacks this one
        ("openai", "not-a-project", "global-openai-key"),  # unknown project
    ],
)
def test_provider_resolution(tmp_path, provider, project_id, expected_key):
    cfg = _load(
        tmp_path,
        providers={
            "openai": {"api_key": "global-openai-key"},
            "deepgram": {"api_key": "global-dg-key"},
        },
        projects={
            "mama-diner": {
                "name": "Mama Diner",
                "providers": {"openai": {"api_key": "mama-openai-key"}},
            }
        },
        default_project="mama-diner",
    )
    assert cfg.default_project == "mama-diner"
    resolved = cfg.get_provider_config_for_project(provider, project_id=project_id)
    assert resolved["api_key"] == expected_key


def test_provider_returns_empty_when_neither_global_nor_project(tmp_path):
    cfg = _load(
        tmp_path,
        providers={},
        projects={"tony-pizza": {"name": "Tony", "providers": {}}},
    )
    assert cfg.default_project is None
    assert cfg.get_provider_config_for_project("openai", project_id="tony-pizza") == {}
