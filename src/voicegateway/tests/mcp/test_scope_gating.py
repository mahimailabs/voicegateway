"""Scope-gating of MCP tools: the default framework-agnostic surface vs admin."""

from __future__ import annotations

from voicegateway.core.auth import ADMIN_SCOPE
from voicegateway.server.mcp.server import _tool_visible, admin_enabled
from voicegateway.server.mcp.tools import ALL_TOOLS


def _scope(name: str) -> str | None:
    return next(t.required_scope for t in ALL_TOOLS if t.name == name)


def test_legacy_and_destructive_tools_are_admin_scoped():
    """Provider-config (legacy) + destructive tools require admin mode."""
    for name in (
        "list_providers",
        "add_provider",
        "delete_provider",
        "test_provider",
        "vg_add_provider",
        "vg_remove_provider",
        "vg_set_provider_key",
        "vg_test_provider_key",
        "register_model",
        "delete_model",
        "delete_project",
        "get_provider_status",
        "delete_rate_card_override",
    ):
        assert _scope(name) == ADMIN_SCOPE, name


def test_framework_agnostic_tools_are_public():
    """Reads + project/budget config are the default (public) surface."""
    for name in (
        "get_health",
        "get_costs",
        "get_logs",
        "get_latency_stats",
        "list_projects",
        "get_project",
        "create_project",
        "list_models",
        "get_rate_card",
        "set_rate_card_override",
    ):
        assert _scope(name) is None, name


def test_tool_visible_filter():
    class _T:
        required_scope: str | None = None

    public = _T()
    admin = _T()
    admin.required_scope = ADMIN_SCOPE

    assert _tool_visible(public, is_admin=False) is True
    assert _tool_visible(public, is_admin=True) is True
    assert _tool_visible(admin, is_admin=False) is False
    assert _tool_visible(admin, is_admin=True) is True


def test_admin_enabled_reads_env(monkeypatch):
    monkeypatch.delenv("VOICEGW_MCP_ADMIN", raising=False)
    assert admin_enabled() is False
    monkeypatch.setenv("VOICEGW_MCP_ADMIN", "1")
    assert admin_enabled() is True
    monkeypatch.setenv("VOICEGW_MCP_ADMIN", "true")
    assert admin_enabled() is True
    monkeypatch.setenv("VOICEGW_MCP_ADMIN", "no")
    assert admin_enabled() is False
