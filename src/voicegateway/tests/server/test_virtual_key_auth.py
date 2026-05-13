"""Tests for the v0.4.0 virtual-key path in the HTTP API auth middleware.

Covers REQ-VG-TENANT-004:

- A valid virtual key resolves and the request reaches the handler.
- A revoked virtual key returns 401.
- A virtual key scoped to tenant A + a body that declares tenant B
  returns 403 with a structured reason (via
  ``check_tenant_body_conflict``).
- An unscoped virtual key allows the body to declare any tenant.

The unit-level helpers in ``voicegateway/core/auth.py`` are exercised
directly without spinning up a FastAPI app where possible.
"""

from __future__ import annotations

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from voicegateway.core.auth import (
    AuthError,
    check_tenant_body_conflict,
    is_virtual_key_token,
    verify_virtual_key,
)
from voicegateway.core.gateway import Gateway
from voicegateway.repository import virtual_keys
from voicegateway.server import build_app

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


def test_is_virtual_key_token_recognizes_vk_prefix():
    assert is_virtual_key_token("Bearer vk_AABBCCDDEEFFGG") is True
    assert is_virtual_key_token("Bearer sk_anything") is False
    assert is_virtual_key_token("Bearer ") is False
    assert is_virtual_key_token(None) is False
    assert is_virtual_key_token("vk_AABBCCDDEEFFGG") is False  # missing Bearer


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


async def test_verify_virtual_key_returns_verified_key(gateway):
    db = await gateway.storage._ensure_initialized()
    created = await virtual_keys.create_virtual_key(db, name="bot", tenant_id="acme")
    verified = await verify_virtual_key(f"Bearer {created.plaintext}", db)
    assert verified.id == created.id
    assert verified.tenant_id == "acme"


async def test_verify_virtual_key_rejects_revoked(gateway):
    db = await gateway.storage._ensure_initialized()
    created = await virtual_keys.create_virtual_key(db, name="bot")
    await virtual_keys.revoke(db, created.id)

    with pytest.raises(AuthError) as ei:
        await verify_virtual_key(f"Bearer {created.plaintext}", db)
    assert ei.value.status_code == 401


async def test_verify_virtual_key_rejects_non_vk_prefix(gateway):
    db = await gateway.storage._ensure_initialized()
    with pytest.raises(AuthError):
        await verify_virtual_key("Bearer sk-static", db)


async def test_verify_virtual_key_rejects_missing_header(gateway):
    db = await gateway.storage._ensure_initialized()
    with pytest.raises(AuthError) as ei:
        await verify_virtual_key(None, db)
    assert ei.value.status_code == 401


# ---------------------------------------------------------------------------
# End-to-end: middleware flow against the FastAPI app
# ---------------------------------------------------------------------------


async def _client(gw: Gateway):
    app = build_app(gw)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def test_virtual_key_authenticates_write_request(gateway):
    """A valid scoped virtual key satisfies the write dep."""
    db = await gateway.storage._ensure_initialized()
    created = await virtual_keys.create_virtual_key(db, name="bot", tenant_id="acme")

    client = await _client(gateway)
    async with client as c:
        resp = await c.post(
            "/v1/providers",
            headers={"Authorization": f"Bearer {created.plaintext}"},
            json={"provider_id": "ollama-x", "provider_type": "ollama", "api_key": ""},
        )
        # /v1/providers requires the write scope. The vk_ path satisfies
        # every scope in v0.4.0 (RBAC scopes on vk are out of scope).
        assert resp.status_code == 200


async def test_virtual_key_revoked_returns_401(gateway):
    db = await gateway.storage._ensure_initialized()
    created = await virtual_keys.create_virtual_key(db, name="bot")
    await virtual_keys.revoke(db, created.id)

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


async def test_virtual_key_marks_last_used(gateway):
    """Successful verify bumps last_used_at via mark_used."""
    db = await gateway.storage._ensure_initialized()
    created = await virtual_keys.create_virtual_key(db, name="bot")
    assert created.row.last_used_at is None

    client = await _client(gateway)
    async with client as c:
        await c.post(
            "/v1/providers",
            headers={"Authorization": f"Bearer {created.plaintext}"},
            json={"provider_id": "ollama-x", "provider_type": "ollama", "api_key": ""},
        )

    # Re-read the row through a fresh connection (the middleware ran
    # mark_used on its own connection; tests run in the same async
    # context so the commit is visible).
    db2 = await gateway.storage._ensure_initialized()
    row = await virtual_keys.get_by_id(db2, created.id)
    assert row is not None
    assert row.last_used_at is not None


async def test_unscoped_virtual_key_does_not_force_tenant(gateway):
    """check_tenant_body_conflict with key_tenant=None lets body pick."""
    db = await gateway.storage._ensure_initialized()
    await virtual_keys.create_virtual_key(db, name="unscoped")

    # Helper-level check; no app exercise needed because the unscoped
    # behavior is enforced inside check_tenant_body_conflict.
    check_tenant_body_conflict(key_tenant_id=None, body_tenant_id="anything")
