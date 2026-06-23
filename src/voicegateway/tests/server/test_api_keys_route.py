"""End-to-end test of the layered api-keys HTTP endpoints."""

from __future__ import annotations

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.server import build_app

_BASE_CONFIG: dict = {
    "providers": {
        "openai": {"api_key": "test-key"},
        "deepgram": {"api_key": "test-key"},
    },
    "models": {
        "stt": {"deepgram/nova-3": {"provider": "deepgram", "model": "nova-3"}},
        "llm": {"openai/gpt-4o-mini": {"provider": "openai", "model": "gpt-4o-mini"}},
        "tts": {},
    },
    "projects": {},
    "fallbacks": {"stt": [], "llm": [], "tts": []},
    "cost_tracking": {"enabled": True},
}


def _write_config(tmp_path) -> str:
    path = tmp_path / "voicegw.yaml"
    with open(path, "w") as f:
        yaml.dump(_BASE_CONFIG, f)
    return str(path)


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "vk-route.db"))
    monkeypatch.delenv("VOICEGW_API_KEY", raising=False)
    return Gateway(config_path=_write_config(tmp_path))


@pytest.fixture
async def client(gateway):
    app = build_app(gateway)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_create_returns_plaintext_and_201(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/api-keys",
        json={"name": "prod-bot", "tenant_id": "acme"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["plaintext"].startswith("vk_")
    assert len(body["plaintext"]) == 35
    assert body["key"]["name"] == "prod-bot"
    assert body["key"]["tenant_id"] == "acme"
    assert body["key"]["revoked_at"] is None
    assert "key_hash" not in body["key"]


async def test_list_includes_created_key(client: AsyncClient) -> None:
    create_resp = await client.post("/v1/api-keys", json={"name": "list-target"})
    created_id = create_resp.json()["key"]["id"]

    list_resp = await client.get("/v1/api-keys")
    assert list_resp.status_code == 200
    body = list_resp.json()
    assert body["total"] >= 1
    assert any(item["id"] == created_id for item in body["items"])


async def test_get_by_id_returns_404_when_missing(client: AsyncClient) -> None:
    response = await client.get("/v1/api-keys/9999")
    assert response.status_code == 404
    payload = response.json()
    assert payload["type"] == "NotFoundError"


async def test_delete_revokes_and_filters_from_active_list(
    client: AsyncClient,
) -> None:
    created = (await client.post("/v1/api-keys", json={"name": "to-revoke"})).json()
    key_id = created["key"]["id"]

    delete_resp = await client.delete(f"/v1/api-keys/{key_id}")
    assert delete_resp.status_code == 204

    active = (await client.get("/v1/api-keys?include_revoked=false")).json()
    assert all(item["id"] != key_id for item in active["items"])

    all_keys = (await client.get("/v1/api-keys?include_revoked=true")).json()
    revoked = next(item for item in all_keys["items"] if item["id"] == key_id)
    assert revoked["revoked_at"] is not None


async def test_create_validation_rejects_empty_name(client: AsyncClient) -> None:
    response = await client.post("/v1/api-keys", json={"name": ""})
    assert response.status_code == 422
    body = response.json()
    assert body["type"] == "RequestValidationError"
