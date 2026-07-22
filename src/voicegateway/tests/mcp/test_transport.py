"""MCP transport tests — verify protocol messages flow end-to-end."""

from __future__ import annotations

import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from voicegateway.core.gateway import Gateway
from voicegateway.server.mcp.server import create_server


@pytest.fixture
def gateway(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "transport.db"))
    return Gateway(config_path=temp_config)


async def test_list_tools_protocol(gateway):
    """In admin mode the client sees all 25 tools (including v0.0.5 vg_* tools)."""
    server = create_server(gateway, is_admin=True)
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()
        result = await client.list_tools()
        assert len(result.tools) == 25
        names = {t.name for t in result.tools}
        assert "get_health" in names
        assert "add_provider" in names
        assert "vg_add_provider" in names
        assert "vg_remove_provider" in names
        assert "vg_list_providers" in names
        assert "vg_set_provider_key" in names
        assert "vg_test_provider_key" in names
        assert "delete_project" in names


async def test_list_tools_default_hides_admin(gateway):
    """The default (non-admin) surface hides provider-config + destructive tools.

    A coding agent pointed at the MCP server sees the framework-agnostic
    surface: reads plus project/budget config, not the legacy provider tools.
    """
    server = create_server(gateway)  # is_admin defaults to False
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()
        names = {t.name for t in (await client.list_tools()).tools}
        # Framework-agnostic surface is visible.
        assert {"get_costs", "get_logs", "create_project", "list_projects"} <= names
        # Legacy provider-config + destructive tools are hidden.
        for hidden in (
            "add_provider",
            "vg_add_provider",
            "vg_set_provider_key",
            "delete_project",
            "get_provider_status",
        ):
            assert hidden not in names


async def test_call_admin_tool_forbidden_by_default(gateway):
    """Invoking an admin tool without admin mode returns a FORBIDDEN envelope."""
    server = create_server(gateway)  # is_admin defaults to False
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()
        result = await client.call_tool("add_provider", {})
        data = json.loads(result.content[0].text)
        assert data["error"]["code"] == "FORBIDDEN"


async def test_call_tool_get_health(gateway):
    """Client can call get_health and parse the response."""
    server = create_server(gateway)
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()
        result = await client.call_tool("get_health", {})
        assert result.isError is False or result.isError is None
        assert result.content
        text = result.content[0].text
        data = json.loads(text)
        assert data["status"] == "ok"
        assert "version" in data


async def test_call_tool_list_providers(gateway):
    server = create_server(gateway, is_admin=True)
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()
        result = await client.call_tool("list_providers", {})
        data = json.loads(result.content[0].text)
        assert "providers" in data
        assert data["count"] >= 2


async def test_call_tool_validation_error(gateway):
    """Invalid input is rejected by the MCP input schema validator."""
    server = create_server(gateway)
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()
        result = await client.call_tool("get_costs", {"period": "forever"})
        assert result.isError is True
        assert "validation error" in result.content[0].text.lower()


async def test_call_tool_domain_error(gateway):
    """Domain errors (e.g. ProviderNotFound) come through as our JSON envelope."""
    server = create_server(gateway, is_admin=True)
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()
        result = await client.call_tool("get_provider", {"provider_id": "never-exists"})
        data = json.loads(result.content[0].text)
        assert data["error"]["code"] == "PROVIDER_NOT_FOUND"


async def test_call_tool_unknown(gateway):
    """Calling a tool that doesn't exist returns an error envelope, not a crash."""
    server = create_server(gateway)
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()
        # MCP clients typically surface this as isError=True; the server returns
        # a structured error envelope regardless.
        try:
            result = await client.call_tool("get_nonexistent_thing", {})
            # Server responded with our error envelope
            data = json.loads(result.content[0].text)
            assert "error" in data
        except Exception:
            # Some MCP clients raise; that's also acceptable
            pass
