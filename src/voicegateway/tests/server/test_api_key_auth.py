"""Tests for the v0.4.0 api-key path in the HTTP API auth middleware."""

from __future__ import annotations

import pytest
import yaml
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from voicegateway.core.auth import (
    AuthError,
    check_tenant_body_conflict,
    is_api_key_token,
    verify_api_key,
)
from voicegateway.core.gateway import Gateway
from voicegateway.repository import api_keys_repository as api_keys
from voicegateway.server import build_app
from voicegateway.server.api._deps import require_scope

_BASE_CONFIG = {
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


def _write_config(tmp_path):
    path = tmp_path / "voicegw.yaml"
    with open(path, "w") as f:
        yaml.dump(_BASE_CONFIG, f)
    return str(path)


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "vk_auth.db"))
    monkeypatch.delenv("VOICEGW_API_KEY", raising=False)
    return Gateway(config_path=_write_config(tmp_path))


# ---------------------------------------------------------------------------
# Unit: helpers in voicegateway/core/auth.py
# ---------------------------------------------------------------------------


def test_is_api_key_token_recognizes_vk_prefix():
    assert is_api_key_token("Bearer vk_AABBCCDDEEFFGG") is True
    assert is_api_key_token("Bearer sk_anything") is False
    assert is_api_key_token("Bearer ") is False
    assert is_api_key_token(None) is False
    assert is_api_key_token("vk_AABBCCDDEEFFGG") is False  # missing Bearer


def test_check_tenant_body_conflict_allows_when_either_none():
    check_tenant_body_conflict(key_tenant_id=None, body_tenant_id="acme")
    check_tenant_body_conflict(key_tenant_id="acme", body_tenant_id=None)
    check_tenant_body_conflict(key_tenant_id=None, body_tenant_id=None)


def test_check_tenant_body_conflict_allows_matching():
    check_tenant_body_conflict(key_tenant_id="acme", body_tenant_id="acme")


def test_check_tenant_body_conflict_rejects_mismatch():
    with pytest.raises(AuthError) as ei:
        check_tenant_body_conflict(key_tenant_id="acme", body_tenant_id="beta")
    assert ei.value.status_code == 403
    assert "acme" in ei.value.message
    assert "beta" in ei.value.message


async def test_verify_api_key_returns_verified_key(gateway):
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        created = await api_keys.create_api_key(db, name="bot", tenant_id="acme")
        verified = await verify_api_key(f"Bearer {created.plaintext}", db)
    assert verified.id == created.id
    assert verified.tenant_id == "acme"


async def test_verify_api_key_rejects_revoked(gateway):
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        created = await api_keys.create_api_key(db, name="bot")
        await api_keys.revoke(db, created.id)
        with pytest.raises(AuthError) as ei:
            await verify_api_key(f"Bearer {created.plaintext}", db)
    assert ei.value.status_code == 401


async def test_verify_api_key_rejects_non_vk_prefix(gateway):
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        with pytest.raises(AuthError):
            await verify_api_key("Bearer sk-static", db)


async def test_verify_api_key_rejects_missing_header(gateway):
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        with pytest.raises(AuthError) as ei:
            await verify_api_key(None, db)
    assert ei.value.status_code == 401


# ---------------------------------------------------------------------------
# End-to-end: middleware flow against the FastAPI app
# ---------------------------------------------------------------------------


async def _client(gw: Gateway):
    app = build_app(gw)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def test_api_key_authenticates_write_request(gateway):
    """A valid scoped virtual key satisfies the write dep."""
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        created = await api_keys.create_api_key(db, name="bot", tenant_id="acme")

    client = await _client(gateway)
    async with client as c:
        resp = await c.post(
            "/v1/providers",
            headers={"Authorization": f"Bearer {created.plaintext}"},
            json={"provider_id": "ollama-x", "provider_type": "ollama", "api_key": ""},
        )
        # /v1/providers requires the write scope. A default vk_ key has
        # wildcard scopes ('*') and tenant role, so it passes write checks.
        assert resp.status_code == 200


async def test_api_key_revoked_returns_401(gateway):
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        created = await api_keys.create_api_key(db, name="bot")
        await api_keys.revoke(db, created.id)

    client = await _client(gateway)
    async with client as c:
        resp = await c.post(
            "/v1/providers",
            headers={"Authorization": f"Bearer {created.plaintext}"},
            json={"provider_id": "ollama-x", "provider_type": "ollama", "api_key": ""},
        )
        # The auth block isn't enforced (no api_keys configured) so a
        # revoked vk_ falls through? No — the middleware short-circuits
        # on vk_ prefix detection and rejects revoked keys directly.
        assert resp.status_code == 401


async def test_api_key_marks_last_used(gateway):
    """Successful verify bumps last_used_at via mark_used."""
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        created = await api_keys.create_api_key(db, name="bot")
    assert created.row.last_used_at is None

    client = await _client(gateway)
    async with client as c:
        await c.post(
            "/v1/providers",
            headers={"Authorization": f"Bearer {created.plaintext}"},
            json={"provider_id": "ollama-x", "provider_type": "ollama", "api_key": ""},
        )

    # Re-read the row through a fresh session.
    async with gateway.storage._conn.session() as db2:
        row = await api_keys.get_by_id(db2, created.id)
    assert row is not None
    assert row.last_used_at is not None


async def test_unscoped_api_key_does_not_force_tenant(gateway):
    """check_tenant_body_conflict with key_tenant=None lets body pick."""
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        await api_keys.create_api_key(db, name="unscoped")

    # Helper-level check; no app exercise needed because the unscoped
    # behavior is enforced inside check_tenant_body_conflict.
    check_tenant_body_conflict(key_tenant_id=None, body_tenant_id="anything")


async def test_api_key_authenticates_write_request_default_scope(gateway):
    """A default (wildcard-scoped) virtual key satisfies the write dep."""
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        created = await api_keys.create_api_key(db, name="bot", tenant_id="acme")

    client = await _client(gateway)
    async with client as c:
        resp = await c.post(
            "/v1/providers",
            headers={"Authorization": f"Bearer {created.plaintext}"},
            json={"provider_id": "ollama-x", "provider_type": "ollama", "api_key": ""},
        )
        assert resp.status_code == 200


async def test_require_scope_admin_denies_tenant_key(gateway):
    """A tenant-role key (even with wildcard scopes) is denied 'admin' scope."""
    # Build a minimal app with an admin-gated endpoint.
    mini = FastAPI()
    mini.state.gateway = gateway
    mini.state.api_keys = []

    @mini.get("/admin-only")
    async def _admin_only(_auth: None = Depends(require_scope("admin"))):
        return JSONResponse({"ok": True})

    transport = ASGITransport(app=mini)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await gateway.storage._ensure_initialized()
        async with gateway.storage._conn.session() as db:
            created = await api_keys.create_api_key(
                db, name="tenant-bot", role="tenant", scopes="*"
            )
        resp = await c.get(
            "/admin-only",
            headers={"Authorization": f"Bearer {created.plaintext}"},
        )
        assert resp.status_code == 403
