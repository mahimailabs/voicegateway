from __future__ import annotations

import pytest
from pydantic import ValidationError

from voicegateway.core.auth import ADMIN_SCOPE
from voicegateway.core.gateway import Gateway
from voicegateway.server.mcp.tools import ALL_TOOLS


def _tool():
    return next(tool for tool in ALL_TOOLS if tool.name == "get_accounting_status")


@pytest.fixture
def gateway(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "accounting-mcp.db"))
    return Gateway(config_path=temp_config)


def test_accounting_tool_is_read_only_operator_scoped() -> None:
    tool = _tool()
    assert tool.required_scope == ADMIN_SCOPE
    assert "never returns acquisition" in tool.description.lower()
    assert "prompt" in tool.description.lower()


async def test_accounting_tool_requires_server_bound_tenant(
    gateway, monkeypatch
) -> None:
    monkeypatch.delenv("VOICEGW_MCP_ACCOUNTING_TENANT", raising=False)
    result = await _tool().handler(gateway, {})
    assert result["error"] == "accounting_tenant_not_configured"


async def test_accounting_tool_returns_selling_status_only(
    gateway, monkeypatch
) -> None:
    monkeypatch.setenv("VOICEGW_MCP_ACCOUNTING_TENANT", "tenant-a")
    result = await _tool().handler(gateway, {})
    assert result["selling_total_usd"] == "0"
    assert "acquisition_total_usd" not in result
    assert "margin_usd" not in result


async def test_accounting_tool_caller_cannot_select_tenant(
    gateway, monkeypatch
) -> None:
    monkeypatch.setenv("VOICEGW_MCP_ACCOUNTING_TENANT", "tenant-a")
    with pytest.raises(ValidationError, match="tenant"):
        await _tool().handler(gateway, {"tenant": "tenant-b"})
