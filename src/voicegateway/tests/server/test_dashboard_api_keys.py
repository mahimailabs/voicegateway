"""Happy-path tests for /api/virtual_keys (create + list + revoke).

The storage-disabled error paths are covered by
:mod:`tests.server.test_dashboard_storage_none`; this file exercises
the create + list + revoke flow against a real Gateway with storage.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from voicegateway.core.gateway import Gateway
from voicegateway.server.main import build_app


@pytest.fixture
def client(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "vk.db"))
    gw = Gateway(config_path=temp_config)
    app = build_app(gw, enable_mcp_sse=False, enable_dashboard=False)
    return TestClient(app)


def test_list_virtual_keys_starts_empty(client) -> None:
    resp = client.get("/api/virtual_keys")
    assert resp.status_code == 200
    assert resp.json() == {"keys": []}


def test_create_virtual_key_returns_plaintext_once(client) -> None:
    resp = client.post(
        "/api/virtual_keys",
        json={"name": "demo-key", "tenant_id": "acme", "issued_by": "ops"},
    )
    assert resp.status_code == 201 or resp.status_code == 200
    body = resp.json()
    assert body["plaintext"].startswith("vk_")
    assert body["row"]["name"] == "demo-key"
    assert body["row"]["tenant_id"] == "acme"
    assert "key_hash" not in body["row"] or body["row"].get("key_hash") is not None


def test_create_virtual_key_rejects_missing_name(client) -> None:
    resp = client.post("/api/virtual_keys", json={})
    assert resp.status_code == 400
    assert "name" in resp.json()["detail"].lower()


def test_create_virtual_key_rejects_whitespace_name(client) -> None:
    resp = client.post("/api/virtual_keys", json={"name": "   "})
    assert resp.status_code == 400


def test_create_virtual_key_strips_empty_optional_fields(client) -> None:
    resp = client.post(
        "/api/virtual_keys",
        json={"name": "minimal", "tenant_id": "", "issued_by": None},
    )
    assert resp.status_code in (200, 201)
    row = resp.json()["row"]
    assert row["tenant_id"] is None
    assert row["issued_by"] is None


def test_list_virtual_keys_surfaces_created_key(client) -> None:
    create = client.post("/api/virtual_keys", json={"name": "to-list"})
    assert create.status_code in (200, 201)
    created_id = create.json()["id"]

    resp = client.get("/api/virtual_keys")
    assert resp.status_code == 200
    keys = resp.json()["keys"]
    assert any(k["id"] == created_id for k in keys)


def test_revoke_virtual_key_returns_revoked_payload(client) -> None:
    create = client.post("/api/virtual_keys", json={"name": "to-revoke"})
    created_id = create.json()["id"]

    resp = client.post(f"/api/virtual_keys/{created_id}/revoke")
    assert resp.status_code == 200
    body = resp.json()
    assert body["revoked"] is True
    assert body["id"] == created_id
    assert body["row"]["revoked_at"] is not None


def test_revoke_unknown_virtual_key_returns_404(client) -> None:
    resp = client.post("/api/virtual_keys/999999/revoke")
    assert resp.status_code == 404


def test_revoke_already_revoked_key_returns_404(client) -> None:
    create = client.post("/api/virtual_keys", json={"name": "double-revoke"})
    created_id = create.json()["id"]
    client.post(f"/api/virtual_keys/{created_id}/revoke")

    resp = client.post(f"/api/virtual_keys/{created_id}/revoke")
    assert resp.status_code == 404
    assert "not found or already revoked" in resp.json()["detail"].lower()


def test_list_virtual_keys_filters_revoked_when_include_revoked_false(client) -> None:
    create = client.post("/api/virtual_keys", json={"name": "hide-me"})
    created_id = create.json()["id"]
    client.post(f"/api/virtual_keys/{created_id}/revoke")

    with_revoked = client.get("/api/virtual_keys?include_revoked=true").json()["keys"]
    without_revoked = client.get("/api/virtual_keys?include_revoked=false").json()[
        "keys"
    ]

    assert any(k["id"] == created_id for k in with_revoked)
    assert all(k["id"] != created_id for k in without_revoked)
