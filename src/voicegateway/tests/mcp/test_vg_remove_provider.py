"""Tests for the v0.0.5 ``vg_remove_provider`` MCP tool.

Per design.md section 3.4. Removes the per-project row written by
vg_add_provider; refuses to remove YAML-defined providers; emits a
clear PROVIDER_NOT_FOUND when the (project, provider) pair has no
matching DB row.
"""

from __future__ import annotations

import pytest

from voicegateway.core.gateway import Gateway
from voicegateway.server.mcp.errors import (
    ProviderNotFoundError,
    ReadOnlyResourceError,
    ValidationError,
)
from voicegateway.server.mcp.tools import ALL_TOOLS


@pytest.fixture
def gateway(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "vg-remove.db"))
    return Gateway(config_path=temp_config)


def _tool(name: str):
    return next(t for t in ALL_TOOLS if t.name == name)


async def _seed_project_key(gateway: Gateway, project: str, provider: str, key: str):
    add = _tool("vg_add_provider")
    await add.handler(
        gateway,
        {"project": project, "provider": provider, "api_key": key},
    )


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------


def test_tool_is_registered():
    names = {t.name for t in ALL_TOOLS}
    assert "vg_remove_provider" in names


def test_tool_input_schema_requires_project_and_provider():
    tool = _tool("vg_remove_provider")
    required = set(tool.input_schema.get("required", []))
    assert "project" in required
    assert "provider" in required


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_removes_seeded_per_project_row(gateway):
    await _seed_project_key(gateway, "tony-pizza", "openai", "sk-tony")

    result = await _tool("vg_remove_provider").handler(
        gateway,
        {"project": "tony-pizza", "provider": "openai"},
    )
    assert result == {
        "project": "tony-pizza",
        "provider": "openai",
        "provider_id": "tony-pizza:openai",
        "deleted": True,
    }

    # Row is gone from storage.
    rows = await gateway.storage.list_managed_providers()
    ids = {r["provider_id"] for r in rows}
    assert "tony-pizza:openai" not in ids


async def test_removes_only_one_project(gateway):
    """Two projects each carry their own openai key. Removing one
    project's must NOT touch the other.
    """
    await _seed_project_key(gateway, "tony-pizza", "openai", "sk-tony")
    await _seed_project_key(gateway, "mama-diner", "openai", "sk-mama")

    await _tool("vg_remove_provider").handler(
        gateway,
        {"project": "tony-pizza", "provider": "openai"},
    )

    rows = await gateway.storage.list_managed_providers()
    ids = {r["provider_id"] for r in rows}
    assert "tony-pizza:openai" not in ids
    assert "mama-diner:openai" in ids


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


async def test_unknown_pair_raises_provider_not_found(gateway):
    with pytest.raises(ProviderNotFoundError, match="No managed provider"):
        await _tool("vg_remove_provider").handler(
            gateway,
            {"project": "ghost-project", "provider": "openai"},
        )


async def test_yaml_defined_provider_id_is_read_only(tmp_path, monkeypatch):
    """When the user has a YAML provider whose id collides with the
    composite ``"<project>:<provider>"``, removing it via vg_*
    must refuse and point at YAML.
    """
    import yaml as _yaml

    cfg = {
        "providers": {"tony-pizza:openai": {"api_key": "from-yaml"}},
        "models": {"stt": {}, "llm": {}, "tts": {}},
        "stacks": {},
        "fallbacks": {"stt": [], "llm": [], "tts": []},
        "cost_tracking": {"enabled": True},
        "observability": {"latency_tracking": True},
    }
    cfg_path = tmp_path / "voicegw.yaml"
    cfg_path.write_text(_yaml.dump(cfg))
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "v.db"))
    gw = Gateway(config_path=str(cfg_path))

    with pytest.raises(ReadOnlyResourceError):
        await _tool("vg_remove_provider").handler(
            gw,
            {"project": "tony-pizza", "provider": "openai"},
        )


async def test_storage_disabled_raises_provider_not_found(
    temp_config, tmp_path, monkeypatch
):
    import yaml as _yaml

    cfg = {
        "providers": {"openai": {"api_key": "k"}},
        "models": {"stt": {}, "llm": {}, "tts": {}},
        "stacks": {},
        "fallbacks": {"stt": [], "llm": [], "tts": []},
        "cost_tracking": {"enabled": False},
        "observability": {"latency_tracking": True},
    }
    cfg_path = tmp_path / "no-storage.yaml"
    cfg_path.write_text(_yaml.dump(cfg))
    monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)
    gw = Gateway(config_path=str(cfg_path))
    assert gw.storage is None

    with pytest.raises(ProviderNotFoundError, match="storage disabled"):
        await _tool("vg_remove_provider").handler(
            gw, {"project": "tony", "provider": "openai"}
        )


async def test_missing_required_args_rejected(gateway):
    with pytest.raises(ValidationError):
        await _tool("vg_remove_provider").handler(gateway, {"project": "tony-pizza"})


async def test_double_remove_raises_provider_not_found(gateway):
    """Removing twice — the second call should error since the row's gone."""
    await _seed_project_key(gateway, "tony-pizza", "openai", "sk-tony")
    await _tool("vg_remove_provider").handler(
        gateway,
        {"project": "tony-pizza", "provider": "openai"},
    )
    with pytest.raises(ProviderNotFoundError):
        await _tool("vg_remove_provider").handler(
            gateway,
            {"project": "tony-pizza", "provider": "openai"},
        )
