"""MCP integration test stub for the five v0.0.5 vg_* provider tools.

Per design.md section 3.4. The lower-level handler tests in
``test_vg_add_provider.py`` etc. exercise each tool's logic
directly; this file exercises them through the actual MCP transport
the way Claude Code would call them. Full end-to-end verification
through a live Claude Code session is mahimairaja's manual task
(5.11).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from voicegateway.core.gateway import Gateway
from voicegateway.server.mcp.server import create_server


@pytest.fixture
def gateway(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "vg-mcp-int.db"))
    return Gateway(config_path=temp_config)


VG_TOOLS = [
    "vg_add_provider",
    "vg_remove_provider",
    "vg_list_providers",
    "vg_set_provider_key",
    "vg_test_provider_key",
]


async def test_all_five_vg_tools_listed_via_protocol(gateway):
    """An MCP client sees every v0.0.5 provider tool advertised."""
    server = create_server(gateway, is_admin=True)
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()
        result = await client.list_tools()
        names = {t.name for t in result.tools}
        for name in VG_TOOLS:
            assert name in names, f"missing tool: {name}"


async def test_vg_lifecycle_through_mcp_protocol(gateway):
    """Full add → list → set (rotate) → remove lifecycle, all over MCP."""
    server = create_server(gateway, is_admin=True)
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()

        # 1. Add a key.
        add_result = await client.call_tool(
            "vg_add_provider",
            {
                "project": "tony-pizza",
                "provider": "openai",
                "api_key": "sk-tony-original",
            },
        )
        add_data = json.loads(add_result.content[0].text)
        assert add_data["created"] is True
        assert add_data["provider_id"] == "tony-pizza:openai"

        # 2. List narrowed to that project — should show the row.
        list_result = await client.call_tool(
            "vg_list_providers", {"project": "tony-pizza"}
        )
        list_data = json.loads(list_result.content[0].text)
        assert len(list_data["providers"]) == 1
        assert list_data["providers"][0]["provider_id"] == "tony-pizza:openai"
        assert list_data["providers"][0]["source"] == "db"

        # 3. Rotate the key.
        rotate_result = await client.call_tool(
            "vg_set_provider_key",
            {
                "project": "tony-pizza",
                "provider": "openai",
                "api_key": "sk-tony-rotated",
            },
        )
        rotate_data = json.loads(rotate_result.content[0].text)
        assert rotate_data["rotated"] is True

        # 4. Remove.
        remove_result = await client.call_tool(
            "vg_remove_provider",
            {"project": "tony-pizza", "provider": "openai"},
        )
        remove_data = json.loads(remove_result.content[0].text)
        assert remove_data["deleted"] is True

        # 5. List again — should be empty.
        list_result_2 = await client.call_tool(
            "vg_list_providers", {"project": "tony-pizza"}
        )
        list_data_2 = json.loads(list_result_2.content[0].text)
        assert list_data_2["providers"] == []


async def test_vg_test_provider_key_ok_through_mcp(gateway, monkeypatch):
    """vg_test_provider_key returns ``status: ok`` when the underlying
    provider's health_check returns True. Stubbed via
    voicegateway.core.registry.create_provider so we don't hit a real
    OpenAI endpoint.
    """

    class _Healthy:
        def __init__(self, _config: dict[str, Any]) -> None:
            self._config = _config

        async def health_check(self) -> bool:
            return True

    monkeypatch.setattr(
        "voicegateway.core.registry.create_provider",
        lambda _name, cfg: _Healthy(cfg),
    )

    server = create_server(gateway, is_admin=True)
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()

        await client.call_tool(
            "vg_add_provider",
            {
                "project": "tony-pizza",
                "provider": "openai",
                "api_key": "sk-tony",
            },
        )

        test_result = await client.call_tool(
            "vg_test_provider_key",
            {"project": "tony-pizza", "provider": "openai"},
        )
        data = json.loads(test_result.content[0].text)
        assert data["status"] == "ok"
        assert data["message"] == "reachable"
        assert data["provider_id"] == "tony-pizza:openai"


async def test_vg_remove_unknown_pair_returns_error_envelope(gateway):
    """Calling vg_remove_provider on a non-existent pair returns the
    structured error envelope (not a crash).
    """
    server = create_server(gateway, is_admin=True)
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()
        result = await client.call_tool(
            "vg_remove_provider",
            {"project": "ghost", "provider": "openai"},
        )
        data = json.loads(result.content[0].text)
        assert data["error"]["code"] == "PROVIDER_NOT_FOUND"


async def test_vg_set_provider_key_unknown_pair_returns_error_envelope(gateway):
    """Same envelope shape for vg_set_provider_key on missing rows."""
    server = create_server(gateway, is_admin=True)
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()
        result = await client.call_tool(
            "vg_set_provider_key",
            {"project": "ghost", "provider": "openai", "api_key": "k"},
        )
        data = json.loads(result.content[0].text)
        assert data["error"]["code"] == "PROVIDER_NOT_FOUND"


async def test_vg_add_provider_validation_error_through_protocol(gateway):
    """Unknown provider type → MCP returns isError=True with the
    structured envelope.
    """
    server = create_server(gateway, is_admin=True)
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()
        result = await client.call_tool(
            "vg_add_provider",
            {
                "project": "tony-pizza",
                "provider": "not-a-provider",
                "api_key": "k",
            },
        )
        data = json.loads(result.content[0].text)
        assert "error" in data
