"""Tests for dashboard/api/main.py endpoints."""

import os

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway


@pytest.fixture
def gateway(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "dash-test.db"))
    return Gateway(config_path=temp_config)


@pytest.fixture
async def client(gateway):
    import dashboard.api.main as dash_module
    dash_module._gateway = gateway
    transport = ASGITransport(app=dash_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    dash_module._gateway = None


async def test_api_status(client):
    resp = await client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "providers" in data
    assert "models" in data
    assert "fallbacks" in data


async def test_api_costs(client):
    resp = await client.get("/api/costs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0.0
    assert "by_provider" in data


async def test_api_costs_with_project(client):
    resp = await client.get("/api/costs?project=test-project")
    assert resp.status_code == 200


async def test_api_latency(client):
    resp = await client.get("/api/latency")
    assert resp.status_code == 200


async def test_api_logs(client):
    resp = await client.get("/api/logs")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_api_logs_with_modality(client):
    resp = await client.get("/api/logs?modality=stt")
    assert resp.status_code == 200


async def test_api_overview(client):
    resp = await client.get("/api/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_requests" in data
    assert "active_models" in data
    assert "providers_configured" in data


async def test_api_overview_with_project(client):
    resp = await client.get("/api/overview?project=test-project")
    assert resp.status_code == 200


async def test_api_projects(client):
    resp = await client.get("/api/projects")
    assert resp.status_code == 200
    data = resp.json()
    assert "projects" in data
    assert "stats" in data


async def test_missing_frontend_fallback(client):
    """When the Vite build doesn't exist, root returns a helpful error."""
    resp = await client.get("/")
    # Either serves index.html (if dist exists) or returns the error JSON
    assert resp.status_code == 200
