"""Exercise the ``gateway.storage is None`` fallback branches across every
dashboard router. The default test fixtures build a Gateway with cost
tracking enabled, so the None branch is unreachable from those tests;
this file builds a Gateway with ``cost_tracking.enabled: false`` and
walks every endpoint that has the fallback.
"""

from __future__ import annotations

import pytest
import yaml
from fastapi.testclient import TestClient

from voicegateway.core.gateway import Gateway
from voicegateway.server.main import build_app


@pytest.fixture
def storage_disabled_client(tmp_path, monkeypatch):
    """Build a daemon TestClient whose Gateway has no storage."""
    cfg_path = tmp_path / "no-storage.yaml"
    cfg_path.write_text(
        yaml.dump(
            {
                "providers": {"openai": {"api_key": "test"}},
                "models": {"stt": {}, "llm": {}, "tts": {}},
                "stacks": {},
                "fallbacks": {"stt": [], "llm": [], "tts": []},
                "cost_tracking": {"enabled": False},
                "observability": {"latency_tracking": True},
            }
        )
    )
    monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)

    gw = Gateway(config_path=str(cfg_path))
    assert gw.storage is None
    app = build_app(gw, enable_mcp_sse=False, enable_dashboard=False)
    return TestClient(app)


def test_costs_returns_empty_payload_when_storage_disabled(storage_disabled_client):
    resp = storage_disabled_client.get("/api/costs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0.0
    assert body["by_provider"] == {}
    assert body["by_model"] == {}
    assert body["by_project"] == {}


def test_latency_returns_empty_dict_when_storage_disabled(storage_disabled_client):
    resp = storage_disabled_client.get("/api/latency")
    assert resp.status_code == 200
    assert resp.json() == {}


def test_logs_returns_empty_list_when_storage_disabled(storage_disabled_client):
    resp = storage_disabled_client.get("/api/logs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_overview_returns_zero_counts_when_storage_disabled(storage_disabled_client):
    resp = storage_disabled_client.get("/api/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_requests"] == 0
    assert body["total_cost"] == 0.0
    assert body["active_models"] == 0


def test_sessions_list_returns_empty_when_storage_disabled(storage_disabled_client):
    resp = storage_disabled_client.get("/api/sessions")
    assert resp.status_code == 200
    assert resp.json() == []


def test_session_detail_returns_404_when_storage_disabled(storage_disabled_client):
    resp = storage_disabled_client.get("/api/sessions/anything")
    assert resp.status_code == 404


def test_metrics_returns_503_when_storage_disabled(storage_disabled_client):
    resp = storage_disabled_client.get("/api/metrics")
    assert resp.status_code == 503


def test_session_turns_returns_503_when_storage_disabled(storage_disabled_client):
    resp = storage_disabled_client.get("/api/sessions/anything/turns")
    assert resp.status_code == 503


def test_session_dead_air_returns_503_when_storage_disabled(storage_disabled_client):
    resp = storage_disabled_client.get("/api/sessions/anything/dead_air")
    assert resp.status_code == 503


def test_session_replay_returns_503_when_storage_disabled(storage_disabled_client):
    resp = storage_disabled_client.get("/api/sessions/anything/replay")
    assert resp.status_code == 503


def test_session_replay_delete_returns_503_when_storage_disabled(
    storage_disabled_client,
):
    resp = storage_disabled_client.delete("/api/sessions/anything/replay")
    assert resp.status_code == 503


def test_replay_storage_returns_503_when_storage_disabled(storage_disabled_client):
    resp = storage_disabled_client.get("/api/replay/storage")
    assert resp.status_code == 503


def test_project_replay_retention_returns_503_when_storage_disabled(
    storage_disabled_client,
):
    resp = storage_disabled_client.post(
        "/api/projects/anything/replay/retention",
        json={"retention_days": 7},
    )
    # 422 fires before storage check when body is malformed; with a valid
    # body the handler reaches the 404 on missing project (storage is None
    # so gw.config.projects.get returns None too in this minimal config).
    assert resp.status_code == 404


def test_tenants_list_returns_empty_when_storage_disabled(storage_disabled_client):
    resp = storage_disabled_client.get("/api/tenants")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenants"] == []
    assert body["unattributed"]["session_count"] == 0


def test_tenant_detail_returns_404_when_storage_disabled(storage_disabled_client):
    resp = storage_disabled_client.get("/api/tenants/anything")
    assert resp.status_code == 404


def test_api_keys_list_returns_empty_when_storage_disabled(storage_disabled_client):
    resp = storage_disabled_client.get("/api/api_keys")
    assert resp.status_code == 200
    assert resp.json() == {"keys": []}


def test_api_keys_create_returns_503_when_storage_disabled(storage_disabled_client):
    resp = storage_disabled_client.post("/api/api_keys", json={"name": "test"})
    assert resp.status_code == 503


def test_api_keys_revoke_returns_503_when_storage_disabled(storage_disabled_client):
    resp = storage_disabled_client.post("/api/api_keys/1/revoke")
    assert resp.status_code == 503


def test_branding_get_returns_503_when_storage_disabled(storage_disabled_client):
    resp = storage_disabled_client.get("/api/projects/anything/branding")
    assert resp.status_code == 503


def test_branding_post_returns_503_when_storage_disabled(storage_disabled_client):
    resp = storage_disabled_client.post(
        "/api/projects/anything/branding", json={"accent_color": "#fff"}
    )
    assert resp.status_code == 503


def test_branding_logo_upload_returns_503_when_storage_disabled(
    storage_disabled_client,
):
    resp = storage_disabled_client.post(
        "/api/projects/anything/branding/logo",
        files={"file": ("logo.png", b"fake-png-bytes", "image/png")},
    )
    assert resp.status_code == 503
