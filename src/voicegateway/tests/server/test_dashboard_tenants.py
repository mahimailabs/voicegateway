"""Tests for /api/tenants (list + per-tenant lookup).

Exercises the list and detail handlers under
:mod:`voicegateway.server.api.dashboard.tenants`. The
storage-disabled paths are covered by
:mod:`tests.server.test_dashboard_storage_none`; this file covers
the path where the gateway has storage but no tenants have been
recorded yet (returns the unattributed bucket with zero counts).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from voicegateway.core.gateway import Gateway
from voicegateway.server.main import build_app


@pytest.fixture
def client(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "tenants.db"))
    gw = Gateway(config_path=temp_config)
    app = build_app(gw, enable_mcp_sse=False, enable_dashboard=False)
    return TestClient(app)


def test_list_tenants_returns_unattributed_bucket_when_empty(client) -> None:
    """A fresh storage has no sessions and no tenant rows."""
    resp = client.get("/api/tenants")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenants"] == []
    assert body["unattributed"]["session_count"] == 0
    assert body["unattributed"]["total_cost_usd"] == 0.0


def test_list_tenants_accepts_q_substring_filter(client) -> None:
    """``q`` is a substring match; with no rows it returns empty + zero unattributed."""
    resp = client.get("/api/tenants?q=acme")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenants"] == []


def test_list_tenants_clamps_limit_within_query_validation(client) -> None:
    resp = client.get("/api/tenants?limit=2000")
    assert resp.status_code == 422  # ge=1, le=1000


def test_list_tenants_rejects_q_over_128_chars(client) -> None:
    resp = client.get("/api/tenants?q=" + "x" * 129)
    assert resp.status_code == 422


def test_get_tenant_returns_404_for_unknown_tenant(client) -> None:
    resp = client.get("/api/tenants/ghost-tenant")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()
