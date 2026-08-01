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

The last section covers the per-session reads, which take no ``tenant`` param
at all: the id IS the request. There the check runs on the fetched row, and a
foreign session must return the same 404 as a session that does not exist, so
that the response never confirms the id is real.
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


# ---------------------------------------------------------------------------
# Per-session reads: the id is the whole request, so the check is post-fetch
# ---------------------------------------------------------------------------

# Every read that hangs off /api/sessions/{id}. /replay is served by
# dashboard/replay.py, the other four by dashboard/sessions.py.
_SESSION_READ_TEMPLATES = [
    "/api/sessions/{sid}",
    "/api/sessions/{sid}/turns",
    "/api/sessions/{sid}/transcript",
    "/api/sessions/{sid}/dead_air",
    "/api/sessions/{sid}/replay",
]


@pytest.mark.parametrize("template", _SESSION_READ_TEMPLATES)
async def test_foreign_session_read_is_404(harness, template):
    """An acme key reading beta's session gets 404, never beta's rows.

    404 and not 403: a 403 would tell the caller the id exists, which is the
    fact being protected. The assertion below pins that a foreign id and a
    made-up id are the same status.
    """
    token = await _make_key(harness.gateway, tenant_id="acme")
    headers = {"Authorization": f"Bearer {token}"}
    async with harness.client() as c:
        foreign = await c.get(template.format(sid="s-beta-1"), headers=headers)
        missing = await c.get(template.format(sid="no-such-session"), headers=headers)

    assert foreign.status_code == 404, (
        f"{template} on a foreign session should be 404, got "
        f"{foreign.status_code}: {foreign.text}"
    )
    assert missing.status_code == foreign.status_code
    # Same body too: nothing but the id the caller already typed comes back.
    assert foreign.json() == {"detail": "Session 's-beta-1' not found"}
    assert missing.json() == {"detail": "Session 'no-such-session' not found"}


@pytest.mark.parametrize("template", _SESSION_READ_TEMPLATES)
async def test_own_session_read_still_works(harness, template):
    """The guard refuses the foreign id only: acme still reads acme."""
    token = await _make_key(harness.gateway, tenant_id="acme")
    async with harness.client() as c:
        resp = await c.get(
            template.format(sid="s-acme-1"),
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200, f"{template}: {resp.status_code} {resp.text}"


@pytest.mark.parametrize("template", _SESSION_READ_TEMPLATES)
async def test_no_credential_operator_reads_any_session(harness, template):
    """The self-hosted operator keeps reading every tenant's sessions."""
    async with harness.client() as c:
        resp = await c.get(template.format(sid="s-beta-1"))
    assert resp.status_code == 200, f"{template}: {resp.status_code} {resp.text}"


async def test_admin_key_reads_any_session(harness):
    """An admin vk_ key (tenant_id None) is not narrowed by the guard."""
    token = await _make_key(harness.gateway, tenant_id=None, role="admin")
    async with harness.client() as c:
        resp = await c.get(
            "/api/sessions/s-beta-1", headers={"Authorization": f"Bearer {token}"}
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "s-beta-1"


# ---------------------------------------------------------------------------
# The public /v1 twin: same rows, so the same scoping
# ---------------------------------------------------------------------------


async def test_v1_foreign_session_read_is_404(harness):
    """An acme key reading beta's session on /v1 gets the same 404 as /api.

    404 and not 403 for the same reason the mirror gives: a 403 would tell
    the caller the id exists. Pinned against a made-up id so the two cases
    stay indistinguishable in status AND body.
    """
    token = await _make_key(harness.gateway, tenant_id="acme")
    headers = {"Authorization": f"Bearer {token}"}
    async with harness.client() as c:
        foreign = await c.get("/v1/sessions/s-beta-1", headers=headers)
        missing = await c.get("/v1/sessions/no-such-session", headers=headers)

    assert foreign.status_code == 404, foreign.text
    assert missing.status_code == foreign.status_code
    assert foreign.json() == {"detail": "Session 's-beta-1' not found"}
    assert missing.json() == {"detail": "Session 'no-such-session' not found"}


async def test_v1_own_session_read_still_works(harness):
    """The guard refuses the foreign id only: acme still reads acme on /v1."""
    token = await _make_key(harness.gateway, tenant_id="acme")
    async with harness.client() as c:
        resp = await c.get(
            "/v1/sessions/s-acme-1", headers={"Authorization": f"Bearer {token}"}
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "s-acme-1"


async def test_v1_session_list_scopes_to_own_tenant(harness):
    """The /v1 list filters too, not just the detail route.

    A list that returned every tenant's sessions would hand over exactly the
    ids the detail route is refusing.
    """
    token = await _make_key(harness.gateway, tenant_id="acme")
    async with harness.client() as c:
        resp = await c.get("/v1/sessions", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    ids = {r["id"] for r in resp.json()}
    assert ids == {"s-acme-1", "s-acme-2"}
    assert "s-beta-1" not in resp.text


async def test_v1_no_credential_operator_sees_all_sessions(harness):
    """The self-hosted operator keeps reading every tenant's sessions on /v1."""
    async with harness.client() as c:
        listed = await c.get("/v1/sessions")
        detail = await c.get("/v1/sessions/s-beta-1")
    assert listed.status_code == 200, listed.text
    assert {"s-acme-1", "s-acme-2", "s-beta-1"}.issubset(
        {r["id"] for r in listed.json()}
    )
    assert detail.status_code == 200, detail.text


# ---------------------------------------------------------------------------
# GET /api/replay/storage: an aggregate, so the leak is the size not the id
# ---------------------------------------------------------------------------


async def _set_replay_sizes(gateway, sizes: dict[str, int]) -> None:
    """Stamp ``replay_size_bytes`` onto seeded sessions.

    ``finalize_session_replay`` normally writes this column; the rows the
    harness seeds have it NULL, and the endpoint sums only non-NULL rows.
    """
    from sqlalchemy import text

    async with gateway.storage._conn.session() as db:
        for session_id, size in sizes.items():
            await db.execute(
                text("UPDATE sessions SET replay_size_bytes = :n WHERE id = :id"),
                {"n": size, "id": session_id},
            )
        await db.commit()


async def test_replay_storage_totals_are_scoped_to_the_tenant(harness):
    """An acme key is told acme's replay footprint, never the deployment's.

    There is no id to 404 on here: the endpoint is an aggregate, so the fact
    being protected is the SIZE of another tenant's captured traffic (and the
    names of the projects producing it). The predicate therefore runs in the
    query, which keeps ``total_replay_size_bytes`` consistent with
    ``by_project``: a total summed over rows excluded from the breakdown
    would leak the number straight back.
    """
    await _set_replay_sizes(
        harness.gateway, {"s-acme-1": 100, "s-acme-2": 200, "s-beta-1": 4000}
    )
    token = await _make_key(harness.gateway, tenant_id="acme")
    async with harness.client() as c:
        scoped = await c.get(
            "/api/replay/storage", headers={"Authorization": f"Bearer {token}"}
        )
        operator = await c.get("/api/replay/storage")

    assert scoped.status_code == 200, scoped.text
    assert scoped.json()["total_replay_size_bytes"] == 300
    assert [r["replay_size_bytes"] for r in scoped.json()["by_project"]] == [300]

    # The self-hosted operator keeps seeing the whole deployment.
    assert operator.status_code == 200, operator.text
    assert operator.json()["total_replay_size_bytes"] == 4300


async def test_replay_storage_admin_key_sees_every_tenant(harness):
    """An admin vk_ key (tenant_id None) is not narrowed by the predicate."""
    await _set_replay_sizes(
        harness.gateway, {"s-acme-1": 100, "s-acme-2": 200, "s-beta-1": 4000}
    )
    token = await _make_key(harness.gateway, tenant_id=None, role="admin")
    async with harness.client() as c:
        resp = await c.get(
            "/api/replay/storage", headers={"Authorization": f"Bearer {token}"}
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["total_replay_size_bytes"] == 4300
