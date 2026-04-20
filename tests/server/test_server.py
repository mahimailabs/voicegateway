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


async def test_v1_latency_includes_percentiles(client, gateway):
    """/v1/latency now returns percentile buckets per model."""
    import time
    import uuid

    from voicegateway.storage.models import RequestRecord

    now = time.time()
    for i in range(1, 21):
        await gateway.storage.log_request(RequestRecord(
            id=str(uuid.uuid4()), timestamp=now - i,
            modality="stt", model_id="deepgram/nova-3",
            provider="deepgram",
            ttfb_ms=float(i * 10),
            total_latency_ms=float(i * 20),
        ))
    resp = await client.get("/v1/latency")
    data = resp.json()
    assert "deepgram/nova-3" in data
    entry = data["deepgram/nova-3"]
    assert set(entry["ttfb_percentiles"].keys()) == {"p50", "p95", "p99"}
    assert entry["ttfb_percentiles"]["p95"] > entry["ttfb_percentiles"]["p50"]


async def test_v1_metrics_emits_latency_summary(client, gateway):
    import time
    import uuid

    from voicegateway.storage.models import RequestRecord

    now = time.time()
    for i in range(1, 11):
        await gateway.storage.log_request(RequestRecord(
            id=str(uuid.uuid4()), timestamp=now - i,
            modality="llm", model_id="openai/gpt-4o-mini",
            provider="openai",
            ttfb_ms=float(i * 50),
            total_latency_ms=float(i * 100),
        ))
    resp = await client.get("/v1/metrics")
    text = resp.text
    assert "voicegw_request_ttfb_seconds" in text
    assert "voicegw_request_total_latency_seconds" in text
    assert 'quantile="0.5"' in text
    assert 'quantile="0.95"' in text
    assert 'quantile="0.99"' in text
    assert 'model="openai/gpt-4o-mini"' in text


# --------------------------------------------------------------------
# CRUD — Providers
# --------------------------------------------------------------------


async def test_list_providers(client):
    resp = await client.get("/v1/providers")
    assert resp.status_code == 200
    assert "providers" in resp.json()


async def test_create_provider(client):
    resp = await client.post("/v1/providers", json={
        "provider_id": "ollama-test",
        "provider_type": "ollama",
        "api_key": "",
    })
    assert resp.status_code == 200
    assert resp.json()["source"] == "db"


async def test_create_provider_yaml_conflict(client):
    resp = await client.post("/v1/providers", json={
        "provider_id": "openai",
        "provider_type": "openai",
        "api_key": "sk-test",
    })
    assert resp.status_code == 409


async def test_create_provider_bad_type(client):
    resp = await client.post("/v1/providers", json={
        "provider_id": "bad",
        "provider_type": "nonexistent",
        "api_key": "",
    })
    assert resp.status_code == 400


async def test_delete_provider_preview(client):
    await client.post("/v1/providers", json={
        "provider_id": "del-me", "provider_type": "ollama", "api_key": "",
    })
    resp = await client.delete("/v1/providers/del-me")
    assert resp.status_code == 200
    assert "would_delete" in resp.json()


async def test_delete_provider_confirm(client):
    await client.post("/v1/providers", json={
        "provider_id": "del-me2", "provider_type": "ollama", "api_key": "",
    })
    resp = await client.delete("/v1/providers/del-me2?confirm=true")
    assert resp.status_code == 200
    assert resp.json()["deleted"] == "del-me2"


async def test_delete_provider_yaml_forbidden(client):
    resp = await client.delete("/v1/providers/openai?confirm=true")
    assert resp.status_code == 403


async def test_patch_provider(client):
    await client.post("/v1/providers", json={
        "provider_id": "patch-me", "provider_type": "ollama", "api_key": "",
    })
    resp = await client.patch("/v1/providers/patch-me", json={"base_url": "http://new:11434"})
    assert resp.status_code == 200
    assert resp.json()["updated"] is True


async def test_test_provider_not_found(client):
    resp = await client.post("/v1/providers/nonexistent/test")
    assert resp.status_code == 404


# --------------------------------------------------------------------
# CRUD — Models
# --------------------------------------------------------------------


async def test_create_model(client):
    resp = await client.post("/v1/models", json={
        "modality": "llm",
        "provider_id": "openai",
        "model_name": "gpt-5-test",
    })
    assert resp.status_code == 200
    assert resp.json()["model_id"] == "openai/gpt-5-test"


async def test_create_model_yaml_conflict(client):
    resp = await client.post("/v1/models", json={
        "modality": "llm",
        "provider_id": "openai",
        "model_name": "gpt-4o-mini",
    })
    assert resp.status_code == 409


async def test_delete_model_yaml_forbidden(client):
    resp = await client.delete("/v1/models/deepgram/nova-3?confirm=true")
    assert resp.status_code == 403


async def test_delete_model_confirm(client):
    await client.post("/v1/models", json={
        "modality": "llm",
        "provider_id": "openai",
        "model_name": "to-delete",
    })
    resp = await client.delete("/v1/models/openai/to-delete?confirm=true")
    assert resp.status_code == 200
    assert resp.json()["deleted"] == "openai/to-delete"


# --------------------------------------------------------------------
# CRUD — Projects
# --------------------------------------------------------------------


async def test_create_project(client):
    resp = await client.post("/v1/projects", json={
        "project_id": "http-proj",
        "name": "HTTP Project",
    })
    assert resp.status_code == 200
    assert resp.json()["source"] == "db"


async def test_create_project_conflict(client):
    resp = await client.post("/v1/projects", json={
        "project_id": "test-project",
        "name": "dup",
    })
    assert resp.status_code == 409


async def test_patch_project(client):
    await client.post("/v1/projects", json={
        "project_id": "update-me", "name": "Original",
    })
    resp = await client.patch("/v1/projects/update-me", json={"name": "Updated"})
    assert resp.status_code == 200
    assert resp.json()["updated"] is True


async def test_delete_project_yaml_forbidden(client):
    resp = await client.delete("/v1/projects/test-project?confirm=true")
    assert resp.status_code == 403


async def test_delete_project_confirm(client):
    await client.post("/v1/projects", json={
        "project_id": "kill-me", "name": "K",
    })
    resp = await client.delete("/v1/projects/kill-me?confirm=true")
    assert resp.status_code == 200
    assert resp.json()["deleted"] == "kill-me"


# --------------------------------------------------------------------
# Audit log
# --------------------------------------------------------------------


async def test_audit_log_records_crud(client):
    await client.post("/v1/providers", json={
        "provider_id": "audit-test", "provider_type": "ollama", "api_key": "",
    })
    resp = await client.get("/v1/audit-log?entity_type=provider")
    assert resp.status_code == 200
    entries = resp.json()
    assert any(e["entity_id"] == "audit-test" for e in entries)


async def test_audit_log_empty(client):
    resp = await client.get("/v1/audit-log?entity_type=nonexistent")
    assert resp.status_code == 200
    assert resp.json() == []
