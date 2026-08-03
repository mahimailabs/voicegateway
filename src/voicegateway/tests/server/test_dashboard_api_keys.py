"""Happy-path tests for /api/api_keys (create + list + revoke).

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


def test_list_api_keys_starts_empty(client) -> None:
    resp = client.get("/api/api_keys")
    assert resp.status_code == 200
    assert resp.json() == {"keys": []}


def test_create_api_key_returns_plaintext_once(client) -> None:
    resp = client.post(
        "/api/api_keys",
        json={"name": "demo-key", "tenant_id": "acme", "issued_by": "ops"},
    )
    assert resp.status_code == 201 or resp.status_code == 200
    body = resp.json()
    assert body["plaintext"].startswith("vk_")
    assert body["row"]["name"] == "demo-key"
    assert body["row"]["tenant_id"] == "acme"
    assert "key_hash" not in body["row"] or body["row"].get("key_hash") is not None


def test_create_api_key_rejects_missing_name(client) -> None:
    resp = client.post("/api/api_keys", json={})
    assert resp.status_code == 400
    assert "name" in resp.json()["detail"].lower()


def test_create_api_key_rejects_whitespace_name(client) -> None:
    resp = client.post("/api/api_keys", json={"name": "   "})
    assert resp.status_code == 400


def test_create_api_key_strips_empty_optional_fields(client) -> None:
    resp = client.post(
        "/api/api_keys",
        json={"name": "minimal", "tenant_id": "", "issued_by": None},
    )
    assert resp.status_code in (200, 201)
    row = resp.json()["row"]
    assert row["tenant_id"] is None
    assert row["issued_by"] is None


def test_list_api_keys_surfaces_created_key(client) -> None:
    create = client.post("/api/api_keys", json={"name": "to-list"})
    assert create.status_code in (200, 201)
    created_id = create.json()["id"]

    resp = client.get("/api/api_keys")
    assert resp.status_code == 200
    keys = resp.json()["keys"]
    assert any(k["id"] == created_id for k in keys)


def test_revoke_api_key_returns_revoked_payload(client) -> None:
    create = client.post("/api/api_keys", json={"name": "to-revoke"})
    created_id = create.json()["id"]

    resp = client.post(f"/api/api_keys/{created_id}/revoke")
    assert resp.status_code == 200
    body = resp.json()
    assert body["revoked"] is True
    assert body["id"] == created_id
    assert body["row"]["revoked_at"] is not None


def test_revoke_unknown_api_key_returns_404(client) -> None:
    resp = client.post("/api/api_keys/999999/revoke")
    assert resp.status_code == 404


def test_revoke_already_revoked_key_returns_404(client) -> None:
    create = client.post("/api/api_keys", json={"name": "double-revoke"})
    created_id = create.json()["id"]
    client.post(f"/api/api_keys/{created_id}/revoke")

    resp = client.post(f"/api/api_keys/{created_id}/revoke")
    assert resp.status_code == 404
    assert "not found or already revoked" in resp.json()["detail"].lower()


def test_list_api_keys_filters_revoked_when_include_revoked_false(client) -> None:
    create = client.post("/api/api_keys", json={"name": "hide-me"})
    created_id = create.json()["id"]
    client.post(f"/api/api_keys/{created_id}/revoke")

    with_revoked = client.get("/api/api_keys?include_revoked=true").json()["keys"]
    without_revoked = client.get("/api/api_keys?include_revoked=false").json()["keys"]

    assert any(k["id"] == created_id for k in with_revoked)
    assert all(k["id"] != created_id for k in without_revoked)


# ---------------------------------------------------------------------------
# Auth: the router is admin-gated once auth is enabled
# ---------------------------------------------------------------------------


@pytest.fixture
def app_under_test(temp_config, tmp_path, monkeypatch):
    """A built app the auth tests can stamp ``state.api_keys`` onto."""
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "vk-auth.db"))
    monkeypatch.delenv("VOICEGW_API_KEY", raising=False)
    gw = Gateway(config_path=temp_config)
    return build_app(gw, enable_mcp_sse=False, enable_dashboard=False)


def test_mint_stays_open_when_no_keys_are_configured(app_under_test) -> None:
    """The self-hosted default (no keys configured) is unchanged.

    ``core.auth.check_request`` returns None on an empty key list, so
    ``require_scope(ADMIN_SCOPE)`` is a no-op and the local operator still
    mints a key with no credential.
    """
    assert app_under_test.state.api_keys == []
    client = TestClient(app_under_test)
    created = client.post("/api/api_keys", json={"name": "local-operator"})
    assert created.status_code in (200, 201)
    assert created.json()["plaintext"].startswith("vk_")
    assert client.get("/api/api_keys").status_code == 200


def test_router_requires_admin_when_auth_enabled(app_under_test) -> None:
    """With static keys configured, an unauthenticated caller cannot mint.

    A minted key carries the wildcard scope, so an ungated POST here is a
    write escalation onto every /v1 endpoint. The gate covers the list and
    the revoke too.
    """
    from voicegateway.core.auth import ADMIN_SCOPE, ApiKey

    # A non-vk_ token takes the static-key path (check_request, which reads
    # app.state.api_keys). A vk_ token would take the DB storage path instead.
    app_under_test.state.api_keys = [
        ApiKey(token="admin-secret-token", name="ops", scopes=(ADMIN_SCOPE,))
    ]
    client = TestClient(app_under_test)

    assert client.post("/api/api_keys", json={"name": "stolen"}).status_code == 401
    assert client.get("/api/api_keys").status_code == 401
    assert client.post("/api/api_keys/1/revoke").status_code == 401
    assert (
        client.post(
            "/api/api_keys",
            json={"name": "stolen"},
            headers={"Authorization": "Bearer wrong-token"},
        ).status_code
        == 401
    )

    ok = client.post(
        "/api/api_keys",
        json={"name": "minted-by-admin"},
        headers={"Authorization": "Bearer admin-secret-token"},
    )
    assert ok.status_code in (200, 201)


def test_router_rejects_key_without_admin_scope(app_under_test) -> None:
    """A valid static key lacking the admin scope is authenticated but 403."""
    from voicegateway.core.auth import ADMIN_SCOPE, ApiKey

    app_under_test.state.api_keys = [
        ApiKey(token="read-only-token", name="viewer", scopes=("read",)),
        ApiKey(token="admin-secret-token", name="ops", scopes=(ADMIN_SCOPE,)),
    ]
    client = TestClient(app_under_test)

    denied = client.post(
        "/api/api_keys",
        json={"name": "escalation"},
        headers={"Authorization": "Bearer read-only-token"},
    )
    assert denied.status_code == 403
    assert (
        client.get(
            "/api/api_keys", headers={"Authorization": "Bearer read-only-token"}
        ).status_code
        == 403
    )


def test_minted_tenant_key_cannot_mint_another_key(app_under_test) -> None:
    """A vk_ key minted here defaults to role='tenant', so it cannot re-mint.

    Without this, a single leaked wildcard key would be self-replicating.
    """
    from voicegateway.core.auth import ADMIN_SCOPE, ApiKey

    client = TestClient(app_under_test)
    plaintext = client.post("/api/api_keys", json={"name": "agent"}).json()["plaintext"]

    app_under_test.state.api_keys = [
        ApiKey(token="admin-secret-token", name="ops", scopes=(ADMIN_SCOPE,))
    ]
    denied = client.post(
        "/api/api_keys",
        json={"name": "self-replication"},
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert denied.status_code == 403
