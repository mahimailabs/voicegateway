"""Tests for the optional bearer-token auth layer used by the MCP HTTP transport."""

from __future__ import annotations

import pytest

from voicegateway.mcp.auth import AuthError, check_authorization_header


def test_no_token_set_passes_anything(monkeypatch):
    """When VOICEGW_MCP_TOKEN is unset, auth is disabled — any header passes."""
    monkeypatch.delenv("VOICEGW_MCP_TOKEN", raising=False)
    # No header is fine.
    check_authorization_header(None)
    # Garbage header is fine.
    check_authorization_header("Bearer wrong")
    check_authorization_header("anything")


def test_missing_header_rejected(monkeypatch):
    monkeypatch.setenv("VOICEGW_MCP_TOKEN", "secret")
    with pytest.raises(AuthError) as exc_info:
        check_authorization_header(None)
    assert exc_info.value.status_code == 401
    assert "bearer" in exc_info.value.message.lower()


def test_wrong_scheme_rejected(monkeypatch):
    monkeypatch.setenv("VOICEGW_MCP_TOKEN", "secret")
    with pytest.raises(AuthError):
        check_authorization_header("Basic dXNlcjpwYXNz")


def test_wrong_token_rejected(monkeypatch):
    monkeypatch.setenv("VOICEGW_MCP_TOKEN", "secret")
    with pytest.raises(AuthError) as exc_info:
        check_authorization_header("Bearer wrong")
    assert exc_info.value.status_code == 401
    assert "invalid" in exc_info.value.message.lower()


def test_correct_token_passes(monkeypatch):
    monkeypatch.setenv("VOICEGW_MCP_TOKEN", "secret")
    check_authorization_header("Bearer secret")


def test_empty_token_env_is_disabled(monkeypatch):
    """Empty string token is treated as 'auth disabled' (standard env pattern)."""
    monkeypatch.setenv("VOICEGW_MCP_TOKEN", "")
    # Should NOT raise
    check_authorization_header(None)


def test_uses_constant_time_comparison():
    """Just a smoke check that hmac.compare_digest is on the code path."""
    import inspect

    from voicegateway.mcp import auth

    source = inspect.getsource(auth)
    assert "hmac.compare_digest" in source
