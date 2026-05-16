"""Plumbing test for the ``/api/*`` dashboard router family.

The single endpoint under test is intentionally trivial; this test
proves that ``ApplicationBuilder`` mounts the ``dashboard_router``
correctly with the ``/api`` prefix and that requests through the
daemon's ``TestClient`` reach the dashboard handlers. Once real
endpoints fold in (commits 2 through 6), each gets its own targeted
test; this one stays as a regression guard for the routing wiring.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from voicegateway.core.gateway import Gateway
from voicegateway.server.main import build_app


@pytest.fixture
def gateway(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "dashboard_health.db"))
    return Gateway(config_path=temp_config)


def test_dashboard_health_reachable_via_daemon(gateway) -> None:
    """``/api/_dashboard/health`` returns the static plumbing payload."""
    app = build_app(gateway, enable_mcp_sse=False, enable_dashboard=False)
    client = TestClient(app)

    response = client.get("/api/_dashboard/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_dashboard_router_does_not_shadow_v1(gateway) -> None:
    """``/health`` keeps resolving after the dashboard router lands."""
    app = build_app(gateway, enable_mcp_sse=False, enable_dashboard=False)
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
