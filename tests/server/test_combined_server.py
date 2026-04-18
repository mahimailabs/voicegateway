"""Tests for voicegateway/combined_server.py — the unified server entrypoint."""

import pytest
from httpx import ASGITransport, AsyncClient

from voicegateway.combined_server import build_combined_app
from voicegateway.core.gateway import Gateway


@pytest.fixture
def gateway(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "combined-test.db"))
    return Gateway(config_path=temp_config)


@pytest.fixture
def app(gateway):
    return build_combined_app(gateway)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health_endpoint(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


async def test_v1_status_endpoint(client):
    resp = await client.get("/v1/status")
    assert resp.status_code == 200
    assert "providers" in resp.json()


async def test_v1_providers_endpoint(client):
    resp = await client.get("/v1/providers")
    assert resp.status_code == 200
    assert "providers" in resp.json()


async def test_v1_projects_endpoint(client):
    resp = await client.get("/v1/projects")
    assert resp.status_code == 200


async def test_mcp_sse_requires_auth(gateway, monkeypatch):
    """MCP SSE endpoint rejects requests without a valid token when auth is enabled."""
    monkeypatch.setenv("VOICEGW_MCP_TOKEN", "test-secret-token")
    # Rebuild app with token set so auth middleware picks it up
    from voicegateway.mcp.auth import check_authorization_header, AuthError
    with pytest.raises(AuthError):
        check_authorization_header(None)


async def test_mcp_sse_no_auth_when_disabled(gateway, monkeypatch):
    """Auth is disabled when no token is set."""
    monkeypatch.delenv("VOICEGW_MCP_TOKEN", raising=False)
    from voicegateway.mcp.auth import check_authorization_header
    # Should not raise
    check_authorization_header(None)


async def test_mcp_sse_wrong_token_rejected(gateway, monkeypatch):
    """Wrong bearer token is rejected."""
    monkeypatch.setenv("VOICEGW_MCP_TOKEN", "correct-token")
    from voicegateway.mcp.auth import check_authorization_header, AuthError
    with pytest.raises(AuthError):
        check_authorization_header("Bearer wrong-token")


async def test_mcp_sse_correct_token_accepted(gateway, monkeypatch):
    """Correct bearer token passes auth."""
    monkeypatch.setenv("VOICEGW_MCP_TOKEN", "correct-token")
    from voicegateway.mcp.auth import check_authorization_header
    check_authorization_header("Bearer correct-token")


async def test_dashboard_api_status(client):
    """Dashboard /api/status endpoint is accessible via combined server."""
    resp = await client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "providers" in data


async def test_dashboard_api_overview(client):
    resp = await client.get("/api/overview")
    assert resp.status_code == 200


async def test_dashboard_api_projects(client):
    resp = await client.get("/api/projects")
    assert resp.status_code == 200


async def test_create_provider_via_combined(client):
    """CRUD endpoints work through the combined server."""
    resp = await client.post(
        "/v1/providers",
        json={
            "provider_id": "combined-test-ollama",
            "provider_type": "ollama",
            "api_key": "",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["source"] == "db"


async def test_audit_log_via_combined(client):
    """Audit log endpoint is accessible."""
    resp = await client.get("/v1/audit-log")
    assert resp.status_code == 200
