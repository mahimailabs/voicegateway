"""Tests for the v0.0.5 ``vg_set_provider_key`` MCP tool.

Per design.md section 3.4. Rotation path: requires the
``"<project>:<provider>"`` row to already exist; raises
PROVIDER_NOT_FOUND otherwise so callers don't silently create new
rows when they meant to rotate.
"""

from __future__ import annotations

import pytest
import yaml

from voicegateway.core.crypto import decrypt
from voicegateway.core.gateway import Gateway
from voicegateway.mcp.errors import (
    ProviderNotFoundError,
    ReadOnlyResourceError,
    ValidationError,
)
from voicegateway.mcp.tools import ALL_TOOLS


def _tool(name: str):
    return next(t for t in ALL_TOOLS if t.name == name)


@pytest.fixture
def gateway(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "vg-set.db"))
    return Gateway(config_path=temp_config)


async def _seed_project_key(
    gateway: Gateway, project: str, provider: str, key: str, base_url: str | None = None
):
    args: dict = {"project": project, "provider": provider, "api_key": key}
    if base_url is not None:
        args["base_url"] = base_url
    await _tool("vg_add_provider").handler(gateway, args)


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------


def test_tool_is_registered():
    names = {t.name for t in ALL_TOOLS}
    assert "vg_set_provider_key" in names


def test_tool_input_schema_has_required_fields():
    tool = _tool("vg_set_provider_key")
    required = set(tool.input_schema.get("required", []))
    assert "project" in required
    assert "provider" in required
    assert "api_key" in required


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_rotates_existing_key(gateway):
    await _seed_project_key(gateway, "tony-pizza", "openai", "sk-original")

    result = await _tool("vg_set_provider_key").handler(
        gateway,
        {
            "project": "tony-pizza",
            "provider": "openai",
            "api_key": "sk-rotated",
        },
    )
    assert result["rotated"] is True
    assert result["provider_id"] == "tony-pizza:openai"

    # Verify the persisted ciphertext decrypts to the new value.
    rows = await gateway.storage.list_managed_providers()
    saved = next(r for r in rows if r["provider_id"] == "tony-pizza:openai")
    assert decrypt(saved["api_key_encrypted"]) == "sk-rotated"


async def test_rotation_does_not_create_extra_rows(gateway):
    await _seed_project_key(gateway, "tony-pizza", "openai", "sk-1")
    await _tool("vg_set_provider_key").handler(
        gateway,
        {"project": "tony-pizza", "provider": "openai", "api_key": "sk-2"},
    )
    rows = await gateway.storage.list_managed_providers()
    matching = [r for r in rows if r["provider_id"] == "tony-pizza:openai"]
    assert len(matching) == 1


async def test_rotation_preserves_base_url_when_omitted(gateway):
    """base_url=None on input means \"no change\", not \"clear it\"."""
    await _seed_project_key(
        gateway,
        "tony-pizza",
        "openai",
        "sk-1",
        base_url="https://my-proxy.example",
    )
    await _tool("vg_set_provider_key").handler(
        gateway,
        {"project": "tony-pizza", "provider": "openai", "api_key": "sk-2"},
    )
    rows = await gateway.storage.list_managed_providers()
    saved = next(r for r in rows if r["provider_id"] == "tony-pizza:openai")
    assert saved["base_url"] == "https://my-proxy.example"


async def test_rotation_updates_base_url_when_given(gateway):
    await _seed_project_key(
        gateway,
        "tony-pizza",
        "openai",
        "sk-1",
        base_url="https://old-proxy.example",
    )
    await _tool("vg_set_provider_key").handler(
        gateway,
        {
            "project": "tony-pizza",
            "provider": "openai",
            "api_key": "sk-2",
            "base_url": "https://new-proxy.example",
        },
    )
    rows = await gateway.storage.list_managed_providers()
    saved = next(r for r in rows if r["provider_id"] == "tony-pizza:openai")
    assert saved["base_url"] == "https://new-proxy.example"


async def test_response_masks_new_key(gateway):
    await _seed_project_key(gateway, "tony-pizza", "openai", "sk-1")
    result = await _tool("vg_set_provider_key").handler(
        gateway,
        {"project": "tony-pizza", "provider": "openai", "api_key": "sk-rotated-secret"},
    )
    assert result["api_key_masked"] != "sk-rotated-secret"
    assert result["api_key_masked"] is not None


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


async def test_unknown_pair_raises_provider_not_found(gateway):
    """Rotating without first adding must error — that's the design
    distinction from vg_add_provider.
    """
    with pytest.raises(ProviderNotFoundError, match="Use vg_add_provider"):
        await _tool("vg_set_provider_key").handler(
            gateway,
            {
                "project": "ghost-project",
                "provider": "openai",
                "api_key": "sk-anything",
            },
        )


async def test_unknown_provider_type_rejected(gateway):
    with pytest.raises(ValidationError, match="Unknown provider"):
        await _tool("vg_set_provider_key").handler(
            gateway,
            {
                "project": "tony-pizza",
                "provider": "not-a-provider",
                "api_key": "k",
            },
        )


async def test_yaml_defined_provider_id_is_read_only(tmp_path, monkeypatch):
    cfg = {
        "providers": {"tony-pizza:openai": {"api_key": "from-yaml"}},
        "models": {"stt": {}, "llm": {}, "tts": {}},
        "stacks": {},
        "fallbacks": {"stt": [], "llm": [], "tts": []},
        "cost_tracking": {"enabled": True},
        "observability": {"latency_tracking": True},
    }
    cfg_path = tmp_path / "voicegw.yaml"
    cfg_path.write_text(yaml.dump(cfg))
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "v.db"))
    gw = Gateway(config_path=str(cfg_path))

    with pytest.raises(ReadOnlyResourceError):
        await _tool("vg_set_provider_key").handler(
            gw,
            {
                "project": "tony-pizza",
                "provider": "openai",
                "api_key": "sk-cant-rotate-yaml",
            },
        )


async def test_storage_disabled_raises_provider_not_found(tmp_path, monkeypatch):
    cfg = {
        "providers": {"openai": {"api_key": "k"}},
        "models": {"stt": {}, "llm": {}, "tts": {}},
        "stacks": {},
        "fallbacks": {"stt": [], "llm": [], "tts": []},
        "cost_tracking": {"enabled": False},
        "observability": {"latency_tracking": True},
    }
    cfg_path = tmp_path / "no-storage.yaml"
    cfg_path.write_text(yaml.dump(cfg))
    monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)
    gw = Gateway(config_path=str(cfg_path))
    assert gw.storage is None

    with pytest.raises(ProviderNotFoundError, match="storage disabled"):
        await _tool("vg_set_provider_key").handler(
            gw, {"project": "tony", "provider": "openai", "api_key": "k"}
        )


async def test_missing_required_args_rejected(gateway):
    with pytest.raises(ValidationError):
        await _tool("vg_set_provider_key").handler(
            gateway, {"project": "tony-pizza", "provider": "openai"}
        )
