"""Tests for voicegateway/server.py — HTTP API endpoints."""


import pytest
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.server import build_app


@pytest.fixture
def gateway(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "server-test.db"))
    return Gateway(config_path=temp_config)


@pytest.fixture
def app(gateway):
    return build_app(gateway)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "uptime_seconds" in data
    assert data["version"] == "0.1.0"


async def test_v1_status(client):
    resp = await client.get("/v1/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "providers" in data
    assert "model_count" in data
    assert "project_count" in data


async def test_v1_models(client):
    resp = await client.get("/v1/models")
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data


async def test_v1_models_with_project_filter(client):
    resp = await client.get("/v1/models?project=test-project")
    assert resp.status_code == 200
    data = resp.json()
    assert data["project"] == "test-project"


async def test_v1_costs_empty(client):
    resp = await client.get("/v1/costs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0.0
    assert "by_provider" in data


async def test_v1_costs_with_period(client):
    resp = await client.get("/v1/costs?period=week")
    assert resp.status_code == 200
    assert resp.json()["period"] == "week"


async def test_v1_costs_with_project(client):
    resp = await client.get("/v1/costs?project=test-project")
    assert resp.status_code == 200


async def test_v1_latency_empty(client):
    resp = await client.get("/v1/latency")
    assert resp.status_code == 200


async def test_v1_projects(client):
    resp = await client.get("/v1/projects")
    assert resp.status_code == 200
    data = resp.json()
    assert "projects" in data
    project_ids = [p["id"] for p in data["projects"]]
    assert "test-project" in project_ids


async def test_v1_project_detail(client):
    resp = await client.get("/v1/projects/test-project")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "test-project"
    assert data["daily_budget"] == 10.0
    assert "budget_action" in data
    assert "budget_status" in data
    assert "today_spend" in data


async def test_v1_project_not_found(client):
    resp = await client.get("/v1/projects/nonexistent")
    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data


async def test_v1_logs_empty(client):
    resp = await client.get("/v1/logs")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_v1_logs_with_filters(client):
    resp = await client.get("/v1/logs?limit=10&modality=stt")
    assert resp.status_code == 200


async def test_v1_metrics(client):
    resp = await client.get("/v1/metrics")
    assert resp.status_code == 200
    assert "voicegw_uptime_seconds" in resp.text
    assert "voicegw_providers_configured" in resp.text
