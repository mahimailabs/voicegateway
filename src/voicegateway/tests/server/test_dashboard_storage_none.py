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


@pytest.mark.parametrize(
    ("method", "path", "body", "status", "payload"),
    [
        ("get", "/api/latency", None, 200, {}),
        ("get", "/api/sessions", None, 200, []),
        ("get", "/api/sessions/anything", None, 404, None),
        ("get", "/api/metrics", None, 503, None),
        ("get", "/api/sessions/anything/turns", None, 503, None),
        ("get", "/api/sessions/anything/dead_air", None, 503, None),
        ("get", "/api/sessions/anything/replay", None, 503, None),
        ("delete", "/api/sessions/anything/replay", None, 503, None),
        ("get", "/api/replay/storage", None, 503, None),
        # Missing project 404s before the storage check.
        (
            "post",
            "/api/projects/anything/replay/retention",
            {"retention_days": 7},
            404,
            None,
        ),
        ("get", "/api/api_keys", None, 200, {"keys": []}),
        ("post", "/api/api_keys", {"name": "test", "scopes": "read"}, 503, None),
        ("post", "/api/api_keys/1/revoke", None, 503, None),
    ],
)
def test_endpoints_degrade_when_storage_disabled(
    storage_disabled_client, method, path, body, status, payload
):
    kwargs = {"json": body} if body is not None else {}
    resp = getattr(storage_disabled_client, method)(path, **kwargs)
    assert resp.status_code == status
    if payload is not None:
        assert resp.json() == payload


def test_overview_returns_zero_counts_when_storage_disabled(storage_disabled_client):
    resp = storage_disabled_client.get("/api/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_requests"] == 0
    assert body["total_cost"] == 0.0
    assert body["active_models"] == 0
