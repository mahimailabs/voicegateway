"""Read-side tenant isolation (Task 7, SECURITY-CRITICAL).

These tests run on the SQLite path (ch_client absent, the default) so they
execute on every run (no ``@pytest.mark.integration``). They assert that the
dashboard read endpoints derive the read tenant from the authenticated
principal, never from the raw caller-supplied ``tenant`` query param:

- an ``acme`` vk_ tenant key asking for ``?tenant=beta`` is refused (403),
  never served beta rows;
- the same key omitting the param is scoped to acme (never all-tenants);
- a no-credential request keeps today's operator behavior (sees all rows);
- the admin cross-tenant endpoint is gated: a tenant key gets 403 before any
  backend-availability (503) check, an admin key gets 503 when ClickHouse is
  absent (the SQLite self-hoster default).
"""

from __future__ import annotations

import os
import tempfile
import time

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.inference.session.context import reset_tenant_id, set_tenant
from voicegateway.models.request_model import RequestRecord
from voicegateway.repository import api_keys_repository as api_keys
from voicegateway.server import build_app

# Endpoints that route a caller ``tenant`` through resolve_read_tenant.
_READ_PATHS = [
    "/api/sessions",
    "/api/costs",
    "/api/latency",
    "/api/logs",
    "/api/metrics",
]


def _record(session_id: str, *, cost: float = 0.05, ts: float | None = None):
    return RequestRecord(
        id=f"req-{session_id}",
        timestamp=ts if ts is not None else time.time(),
        project="default",
        modality="stt",
        model_id="deepgram/nova-3",
        provider="deepgram",
        input_units=0,
        output_units=0,
        cost_usd=cost,
        pricing_source="test",
        ttfb_ms=100.0,
        total_latency_ms=200.0,
        status="success",
        fallback_from=None,
        error_message=None,
        metadata=None,
        session_id=session_id,
    )


async def _seed_two_tenants(storage) -> None:
    set_tenant("acme")
    await storage.log_request(_record("s-acme-1", cost=0.10))
    await storage.log_request(_record("s-acme-2", cost=0.20))
    set_tenant("beta")
    await storage.log_request(_record("s-beta-1", cost=1.00))
    reset_tenant_id()


class _Harness:
    """Builds an app + Gateway over a fresh SQLite db, yields a client maker."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        tmp = self._tmp.name
        # Save the prior value so cleanup() can restore it: VOICEGW_DB_PATH
        # wins over the config db_path (core.database), so leaking it would
        # redirect every later test's SQLite engine at this (deleted) file.
        self._prev_db_path = os.environ.get("VOICEGW_DB_PATH")
        os.environ["VOICEGW_DB_PATH"] = os.path.join(tmp, "isolation.db")
        cfg = {
            "providers": {"openai": {"api_key": "test-key"}},
            "models": {"stt": {}, "llm": {}, "tts": {}},
            "projects": {},
            "fallbacks": {"stt": [], "llm": [], "tts": []},
            "cost_tracking": {"enabled": True},
        }
        cfg_path = os.path.join(tmp, "voicegw.yaml")
        with open(cfg_path, "w") as f:
            yaml.dump(cfg, f)
        self.gateway = Gateway(config_path=cfg_path)
        self.app = build_app(self.gateway, enable_mcp_sse=False, enable_dashboard=False)
        # SQLite path: no ClickHouse client bound.
        self.app.state.ch_client = None

    def client(self) -> AsyncClient:
        return AsyncClient(
            transport=ASGITransport(app=self.app), base_url="http://test"
        )

    def cleanup(self) -> None:
        if self._prev_db_path is None:
            os.environ.pop("VOICEGW_DB_PATH", None)
        else:
            os.environ["VOICEGW_DB_PATH"] = self._prev_db_path
        self._tmp.cleanup()


@pytest.fixture
async def harness():
    h = _Harness()
    await h.gateway.storage._ensure_initialized()
    await _seed_two_tenants(h.gateway.storage)
    reset_tenant_id()
    try:
        yield h
    finally:
        reset_tenant_id()
        h.cleanup()


async def _make_key(gateway, *, tenant_id=None, role="tenant"):
    async with gateway.storage._conn.session() as db:
        created = await api_keys.create_api_key(
            db, name=f"k-{tenant_id}-{role}", tenant_id=tenant_id, role=role
        )
    return created.plaintext


@pytest.mark.parametrize("path", _READ_PATHS)
async def test_foreign_tenant_param_is_refused(harness, path):
    """An acme tenant key asking for ?tenant=beta gets 403, never beta rows."""
    token = await _make_key(harness.gateway, tenant_id="acme")
    async with harness.client() as c:
        resp = await c.get(
            f"{path}?tenant=beta", headers={"Authorization": f"Bearer {token}"}
        )
    assert resp.status_code == 403, (
        f"{path}?tenant=beta with acme key should be 403, got {resp.status_code}: "
        f"{resp.text}"
    )


async def test_omitted_param_scopes_to_own_tenant_costs(harness):
    """No tenant param -> acme key sees only acme costs (0.30), never beta/all."""
    token = await _make_key(harness.gateway, tenant_id="acme")
    async with harness.client() as c:
        resp = await c.get("/api/costs", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    # acme total is 0.30; beta is 1.00; all is 1.30. Must be acme-only.
    assert resp.json()["total"] == pytest.approx(0.30)


async def test_omitted_param_scopes_to_own_tenant_sessions(harness):
    """No tenant param -> acme key sees only acme sessions, never beta."""
    token = await _make_key(harness.gateway, tenant_id="acme")
    async with harness.client() as c:
        resp = await c.get(
            "/api/sessions", headers={"Authorization": f"Bearer {token}"}
        )
    assert resp.status_code == 200, resp.text
    ids = {r["id"] for r in resp.json()}
    assert ids == {"s-acme-1", "s-acme-2"}
    assert "s-beta-1" not in ids


async def test_omitted_param_scopes_to_own_tenant_logs(harness):
    """No tenant param -> acme key sees only acme request logs, never beta."""
    token = await _make_key(harness.gateway, tenant_id="acme")
    async with harness.client() as c:
        resp = await c.get("/api/logs", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    sessions = {r["session_id"] for r in resp.json()}
    assert sessions == {"s-acme-1", "s-acme-2"}
    assert "s-beta-1" not in sessions


async def test_no_credential_operator_sees_all(harness):
    """A no-credential request keeps today's behavior: all tenants' rows."""
    async with harness.client() as c:
        costs = await c.get("/api/costs")
        sessions = await c.get("/api/sessions")
    assert costs.status_code == 200
    # all = acme(0.30) + beta(1.00) = 1.30
    assert costs.json()["total"] == pytest.approx(1.30)
    ids = {r["id"] for r in sessions.json()}
    assert {"s-acme-1", "s-acme-2", "s-beta-1"}.issubset(ids)


async def test_admin_route_refuses_tenant_key(harness):
    """A tenant key hitting the admin cross-tenant route gets 403."""
    token = await _make_key(harness.gateway, tenant_id="acme", role="tenant")
    async with harness.client() as c:
        resp = await c.get(
            "/api/admin/costs/by-tenant",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403, resp.text


async def test_admin_route_authz_precedes_backend_availability(harness):
    """An admin key hits the route but gets 503 (ClickHouse absent), not 200.

    Authz (403 for tenants) must precede the backend-availability 503: the
    tenant key above gets 403, the admin key here passes authz and only then
    discovers ClickHouse is not configured.
    """
    token = await _make_key(harness.gateway, role="admin")
    async with harness.client() as c:
        resp = await c.get(
            "/api/admin/costs/by-tenant",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 503, resp.text
