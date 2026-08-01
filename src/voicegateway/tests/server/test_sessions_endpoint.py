"""Tests for /v1/sessions and /v1/sessions/{id} HTTP endpoints.

The last section covers the dashboard mirrors under /api/sessions/{id}*,
which are gated by ``require_principal`` on the router.
"""

from __future__ import annotations

import time
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.models.request_model import RequestRecord
from voicegateway.server import build_app


@pytest.fixture
def gateway(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "sessions-endpoint.db"))
    return Gateway(config_path=temp_config)


@pytest.fixture
def app(gateway):
    return build_app(gateway)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed_session(
    storage,
    sid: str,
    *,
    project: str = "tony-pizza",
    modality: str = "stt",
    cost: float = 0.001,
    ts: float | None = None,
    provider: str = "deepgram",
):
    rec = RequestRecord(
        id=str(uuid.uuid4()),
        timestamp=ts if ts is not None else time.time(),
        modality=modality,
        model_id=f"{provider}/{modality}-test",
        provider=provider,
        project=project,
        cost_usd=cost,
        session_id=sid,
    )
    await storage.log_request(rec)


# ---------------------------------------------------------------------------
# /v1/sessions (list)
# ---------------------------------------------------------------------------


async def test_list_sessions_empty(client):
    resp = await client.get("/v1/sessions")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_list_sessions_returns_seeded_rows(client, gateway):
    await _seed_session(gateway.storage, "vg-a", project="tony-pizza", cost=0.01)
    await _seed_session(gateway.storage, "vg-b", project="mama-diner", cost=0.02)

    resp = await client.get("/v1/sessions")
    assert resp.status_code == 200
    rows = resp.json()
    ids = {r["id"] for r in rows}
    assert ids == {"vg-a", "vg-b"}


async def test_list_sessions_returns_modalities_as_list(client, gateway):
    """The list endpoint returns modalities as a JSON array (not the"""
    await _seed_session(gateway.storage, "vg-multi", modality="stt", cost=0.01)
    await _seed_session(gateway.storage, "vg-multi", modality="llm", cost=0.02)
    await _seed_session(gateway.storage, "vg-multi", modality="tts", cost=0.03)

    resp = await client.get("/v1/sessions")
    rows = resp.json()
    row = next(r for r in rows if r["id"] == "vg-multi")
    assert isinstance(row["modalities"], list)
    assert sorted(row["modalities"]) == ["llm", "stt", "tts"]
    assert row["request_count"] == 3
    assert abs(row["total_cost_usd"] - 0.06) < 1e-9


async def test_list_sessions_orders_newest_first(client, gateway):
    # Seed two sessions with deterministic timestamps.
    await _seed_session(gateway.storage, "vg-old", ts=1700000000.0)
    await _seed_session(gateway.storage, "vg-new", ts=1750000000.0)

    resp = await client.get("/v1/sessions")
    rows = resp.json()
    assert [r["id"] for r in rows] == ["vg-new", "vg-old"]


async def test_list_sessions_limit_parameter(client, gateway):
    for i in range(5):
        await _seed_session(gateway.storage, f"vg-{i}", ts=1700000000.0 + i)

    resp = await client.get("/v1/sessions?limit=2")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2


async def test_list_sessions_project_filter(client, gateway):
    await _seed_session(gateway.storage, "vg-a", project="tony-pizza")
    await _seed_session(gateway.storage, "vg-b", project="mama-diner")
    await _seed_session(gateway.storage, "vg-c", project="tony-pizza")

    resp = await client.get("/v1/sessions?project=tony-pizza")
    rows = resp.json()
    ids = sorted(r["id"] for r in rows)
    assert ids == ["vg-a", "vg-c"]


async def test_list_sessions_limit_validation(client):
    """Out-of-range limit returns 422 (FastAPI Query bounds)."""
    resp = await client.get("/v1/sessions?limit=0")
    assert resp.status_code == 422

    resp = await client.get("/v1/sessions?limit=10000")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# /v1/sessions/{id} (detail)
# ---------------------------------------------------------------------------


async def test_session_detail_returns_session(client, gateway):
    await _seed_session(gateway.storage, "vg-detail", project="default", cost=0.05)

    resp = await client.get("/v1/sessions/vg-detail")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "vg-detail"
    assert data["project"] == "default"
    assert abs(data["total_cost_usd"] - 0.05) < 1e-9
    assert data["request_count"] == 1
    assert isinstance(data["modalities"], list)
    # AC-002.1: detail responses carry per-modality breakdown +
    # providers list. A single-modality session has one entry.
    assert "by_modality" in data
    assert data["by_modality"]["stt"]["request_count"] == 1
    assert abs(data["by_modality"]["stt"]["cost"] - 0.05) < 1e-9
    assert data["providers"] == ["deepgram"]


async def test_session_detail_per_modality_breakdown_aggregates_by_modality(
    client, gateway
):
    """A session with mixed STT / LLM / TTS requests returns one"""
    await _seed_session(
        gateway.storage, "vg-mix", modality="stt", cost=0.01, provider="deepgram"
    )
    await _seed_session(
        gateway.storage, "vg-mix", modality="llm", cost=0.02, provider="openai"
    )
    await _seed_session(
        gateway.storage, "vg-mix", modality="llm", cost=0.03, provider="openai"
    )
    await _seed_session(
        gateway.storage, "vg-mix", modality="tts", cost=0.04, provider="cartesia"
    )

    resp = await client.get("/v1/sessions/vg-mix")
    data = resp.json()

    by_mod = data["by_modality"]
    assert set(by_mod.keys()) == {"stt", "llm", "tts"}
    assert abs(by_mod["stt"]["cost"] - 0.01) < 1e-9
    assert by_mod["stt"]["request_count"] == 1
    assert abs(by_mod["llm"]["cost"] - 0.05) < 1e-9
    assert by_mod["llm"]["request_count"] == 2
    assert abs(by_mod["tts"]["cost"] - 0.04) < 1e-9
    assert by_mod["tts"]["request_count"] == 1

    # Providers list deduplicates and sorts.
    assert data["providers"] == ["cartesia", "deepgram", "openai"]


async def test_session_detail_ended_at_advances_with_each_request(client, gateway):
    """AC-002.3: ended_at tracks last-activity, not first-activity, so"""
    await _seed_session(gateway.storage, "vg-dur", ts=1700000000.0, cost=0.01)
    await _seed_session(gateway.storage, "vg-dur", ts=1700000050.0, cost=0.01)
    await _seed_session(gateway.storage, "vg-dur", ts=1700000123.0, cost=0.01)

    resp = await client.get("/v1/sessions/vg-dur")
    data = resp.json()
    assert data["started_at"] != data["ended_at"]
    # Both are ISO 8601 — string comparison reflects time order.
    assert data["ended_at"] > data["started_at"]


async def test_session_detail_out_of_order_request_does_not_drag_ended_at_back(
    client, gateway
):
    """A late-arriving record with an older timestamp must NOT move"""
    await _seed_session(gateway.storage, "vg-ooo", ts=1700000100.0)
    expected_ended = (await gateway.storage.get_session("vg-ooo"))["ended_at"]
    await _seed_session(gateway.storage, "vg-ooo", ts=1700000050.0)  # earlier!

    resp = await client.get("/v1/sessions/vg-ooo")
    data = resp.json()
    assert data["ended_at"] == expected_ended  # unchanged


# ---------------------------------------------------------------------------
# /v1/sessions ordering (AC-002.3)
# ---------------------------------------------------------------------------


async def test_list_sessions_order_by_cost_desc(client, gateway):
    await _seed_session(gateway.storage, "vg-cheap", cost=0.001, ts=1700000300.0)
    await _seed_session(gateway.storage, "vg-mid", cost=0.05, ts=1700000200.0)
    await _seed_session(gateway.storage, "vg-pricey", cost=0.50, ts=1700000100.0)

    resp = await client.get("/v1/sessions?order_by=cost_desc")
    rows = resp.json()
    assert [r["id"] for r in rows] == ["vg-pricey", "vg-mid", "vg-cheap"]


async def test_list_sessions_order_by_cost_asc(client, gateway):
    await _seed_session(gateway.storage, "vg-cheap", cost=0.001, ts=1700000300.0)
    await _seed_session(gateway.storage, "vg-mid", cost=0.05, ts=1700000200.0)
    await _seed_session(gateway.storage, "vg-pricey", cost=0.50, ts=1700000100.0)

    resp = await client.get("/v1/sessions?order_by=cost_asc")
    rows = resp.json()
    assert [r["id"] for r in rows] == ["vg-cheap", "vg-mid", "vg-pricey"]


async def test_list_sessions_order_by_started_at_asc(client, gateway):
    await _seed_session(gateway.storage, "vg-old", ts=1700000000.0)
    await _seed_session(gateway.storage, "vg-new", ts=1750000000.0)

    resp = await client.get("/v1/sessions?order_by=started_at_asc")
    rows = resp.json()
    assert [r["id"] for r in rows] == ["vg-old", "vg-new"]


async def test_list_sessions_default_order_is_started_at_desc(client, gateway):
    """Omitting order_by must keep the legacy newest-first behaviour."""
    await _seed_session(gateway.storage, "vg-old", ts=1700000000.0, cost=10.0)
    await _seed_session(gateway.storage, "vg-new", ts=1750000000.0, cost=0.01)

    resp = await client.get("/v1/sessions")
    rows = resp.json()
    assert [r["id"] for r in rows] == ["vg-new", "vg-old"]


async def test_list_sessions_invalid_order_by_returns_422(client):
    resp = await client.get("/v1/sessions?order_by=cost_random")
    assert resp.status_code == 422


async def test_session_detail_returns_404_for_missing(client):
    resp = await client.get("/v1/sessions/does-not-exist")
    assert resp.status_code == 404
    detail = resp.json()
    assert "does-not-exist" in detail["detail"]


async def test_session_detail_returns_404_when_storage_disabled(
    temp_config, tmp_path, monkeypatch
):
    """If cost_tracking is disabled (no storage), the endpoint cannot"""
    monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)
    # Build with no DB path and storage disabled by config default; the
    # temp_config fixture has cost_tracking.enabled=True, so override
    # by writing a fresh config without it.
    import yaml as _yaml

    cfg_path = tmp_path / "no-storage.yaml"
    cfg_path.write_text(
        _yaml.dump(
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
    gw = Gateway(config_path=str(cfg_path))
    assert gw.storage is None
    app = build_app(gw)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/v1/sessions/anything")
        assert resp.status_code == 404


async def test_list_sessions_returns_empty_when_storage_disabled(
    temp_config, tmp_path, monkeypatch
):
    monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)
    import yaml as _yaml

    cfg_path = tmp_path / "no-storage.yaml"
    cfg_path.write_text(
        _yaml.dump(
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
    gw = Gateway(config_path=str(cfg_path))
    assert gw.storage is None
    app = build_app(gw)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/v1/sessions")
        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# Auth: every per-session dashboard read is gated once auth is enabled
# ---------------------------------------------------------------------------

# The four reads that hung off a session id without a dependency. The
# transcript read is the fifth; its auth tests live beside its own
# behavior tests in test_sessions_transcript_endpoint.py.
_PER_SESSION_READS = [
    "/api/sessions/vg-gated",
    "/api/sessions/vg-gated/turns",
    "/api/sessions/vg-gated/dead_air",
    "/api/sessions/vg-gated/replay",
]


@pytest.fixture
def gated_app(temp_config, tmp_path, monkeypatch):
    """A built app the auth tests can stamp ``state.api_keys`` onto."""
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "sessions-auth.db"))
    monkeypatch.delenv("VOICEGW_API_KEY", raising=False)
    gw = Gateway(config_path=temp_config)
    return build_app(gw, enable_mcp_sse=False, enable_dashboard=False)


async def test_per_session_reads_stay_open_when_no_keys_are_configured(gated_app):
    """The self-hosted default (no keys configured) is unchanged.

    ``core.auth.check_request`` returns None on an empty key list, so
    ``require_principal`` resolves the operator principal and the local
    operator still reads every session with no credential.
    """
    assert gated_app.state.api_keys == []
    await _seed_session(gated_app.state.gateway.storage, "vg-gated")

    transport = ASGITransport(app=gated_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        for path in _PER_SESSION_READS:
            resp = await c.get(path)
            assert resp.status_code == 200, f"{path}: {resp.status_code} {resp.text}"


async def test_per_session_reads_require_auth_when_enabled(gated_app):
    """With static keys configured, an unauthenticated caller reads nothing.

    Only the list endpoint carried a dependency before; a session id was
    enough to read the detail, the turns, the dead air and the replay of any
    call on the deployment.
    """
    from voicegateway.core.auth import ApiKey

    # A non-vk_ token takes the static-key path (check_request, which reads
    # app.state.api_keys). A vk_ token would take the DB storage path instead.
    gated_app.state.api_keys = [
        ApiKey(token="read-token", name="viewer", scopes=("read",))
    ]
    await _seed_session(gated_app.state.gateway.storage, "vg-gated")

    transport = ASGITransport(app=gated_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        for path in _PER_SESSION_READS:
            anon = await c.get(path)
            assert anon.status_code == 401, f"{path}: {anon.status_code} {anon.text}"

            wrong = await c.get(path, headers={"Authorization": "Bearer nope"})
            assert wrong.status_code == 401, f"{path}: {wrong.status_code}"

            ok = await c.get(path, headers={"Authorization": "Bearer read-token"})
            assert ok.status_code == 200, f"{path}: {ok.status_code} {ok.text}"

        # The already-gated list endpoint keeps the scope it required.
        assert (await c.get("/api/sessions")).status_code == 401
        listed = await c.get(
            "/api/sessions", headers={"Authorization": "Bearer read-token"}
        )
        assert listed.status_code == 200


# ---------------------------------------------------------------------------
# Auth: the public /v1 twin serves the same rows as the gated /api mirror
# ---------------------------------------------------------------------------

_V1_SESSION_READS = [
    "/v1/sessions",
    "/v1/sessions/vg-gated",
]


async def test_v1_session_reads_stay_open_when_no_keys_are_configured(gated_app):
    """The self-hosted default (no keys configured) is unchanged.

    ``core.auth.check_request`` returns None on an empty key list, so
    ``require_principal`` resolves the operator principal and the local
    operator still reads every session with no credential. The behavior tests
    at the top of this file all ride on that path; this one asserts it.
    """
    assert gated_app.state.api_keys == []
    await _seed_session(gated_app.state.gateway.storage, "vg-gated")

    transport = ASGITransport(app=gated_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        for path in _V1_SESSION_READS:
            resp = await c.get(path)
            assert resp.status_code == 200, f"{path}: {resp.status_code} {resp.text}"


async def test_v1_session_reads_require_auth_when_enabled(gated_app):
    """With static keys configured, an unauthenticated caller reads nothing.

    These two routes return exactly the rows the /api mirror returns, so
    leaving them open made the gate on the mirror worth nothing: the same
    session detail was one prefix away.
    """
    from voicegateway.core.auth import ApiKey

    # A non-vk_ token takes the static-key path (check_request, which reads
    # app.state.api_keys). A vk_ token would take the DB storage path instead.
    gated_app.state.api_keys = [
        ApiKey(token="read-token", name="viewer", scopes=("read",))
    ]
    await _seed_session(gated_app.state.gateway.storage, "vg-gated")

    transport = ASGITransport(app=gated_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        for path in _V1_SESSION_READS:
            anon = await c.get(path)
            assert anon.status_code == 401, f"{path}: {anon.status_code} {anon.text}"
            assert "vg-gated" not in anon.text

            wrong = await c.get(path, headers={"Authorization": "Bearer nope"})
            assert wrong.status_code == 401, f"{path}: {wrong.status_code}"

            ok = await c.get(path, headers={"Authorization": "Bearer read-token"})
            assert ok.status_code == 200, f"{path}: {ok.status_code} {ok.text}"


# ---------------------------------------------------------------------------
# Auth: the three ungated siblings on the replay router
# ---------------------------------------------------------------------------
#
# The replay READ was gated on its own; these three were not. They live here
# rather than in test_replay_endpoint.py so every gate this change adds is
# asserted in one place, next to the /api/sessions/{id}/replay read above.


async def test_replay_siblings_stay_open_when_no_keys_are_configured(gated_app):
    """The self-hosted default (no keys configured) is unchanged.

    Both write gates and the storage read are no-ops on an empty key list, so
    the single-operator deployment still deletes a replay, reads the storage
    breakdown and sets a retention window with no credential.
    """
    assert gated_app.state.api_keys == []
    await _seed_session(gated_app.state.gateway.storage, "vg-gated")

    transport = ASGITransport(app=gated_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/api/replay/storage")).status_code == 200
        assert (await c.delete("/api/sessions/vg-gated/replay")).status_code == 200
        retention = await c.post(
            "/api/projects/test-project/replay/retention",
            json={"retention_days": 7},
        )
        assert retention.status_code == 200, retention.text


async def test_replay_storage_read_requires_auth_when_enabled(gated_app):
    """The storage breakdown names every project on the deployment.

    It is a read, so it takes the read dependency the other dashboard reads
    take: a read-scoped token is enough, an anonymous caller is not.
    """
    from voicegateway.core.auth import ApiKey

    gated_app.state.api_keys = [
        ApiKey(token="read-token", name="viewer", scopes=("read",))
    ]
    await _seed_session(gated_app.state.gateway.storage, "vg-gated")

    transport = ASGITransport(app=gated_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/api/replay/storage")).status_code == 401
        wrong = await c.get(
            "/api/replay/storage", headers={"Authorization": "Bearer nope"}
        )
        assert wrong.status_code == 401
        ok = await c.get(
            "/api/replay/storage", headers={"Authorization": "Bearer read-token"}
        )
        assert ok.status_code == 200, ok.text


async def test_replay_writes_require_admin_when_auth_enabled(gated_app):
    """The delete and the retention write take the dashboard write scope.

    Admin, like every other write on a dashboard router. A read-scoped token
    is authenticated but refused (403), an anonymous caller gets 401, and
    neither one destroys the captured payloads or shortens the window that
    decides how long they survive.
    """
    from voicegateway.core.auth import ApiKey

    gated_app.state.api_keys = [
        ApiKey(token="read-token", name="viewer", scopes=("read",)),
        ApiKey(token="admin-token", name="operator", scopes=("admin",)),
    ]
    await _seed_session(gated_app.state.gateway.storage, "vg-gated")
    gateway = gated_app.state.gateway
    before = gateway.config.projects["test-project"].replay.retention_days

    transport = ASGITransport(app=gated_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.delete("/api/sessions/vg-gated/replay")).status_code == 401
        denied = await c.delete(
            "/api/sessions/vg-gated/replay",
            headers={"Authorization": "Bearer read-token"},
        )
        assert denied.status_code == 403, denied.text

        retention_path = "/api/projects/test-project/replay/retention"
        anon = await c.post(retention_path, json={"retention_days": 1})
        assert anon.status_code == 401
        reader = await c.post(
            retention_path,
            json={"retention_days": 1},
            headers={"Authorization": "Bearer read-token"},
        )
        assert reader.status_code == 403, reader.text
        # Nothing moved on either refusal.
        assert gateway.config.projects["test-project"].replay.retention_days == before

        ok_delete = await c.delete(
            "/api/sessions/vg-gated/replay",
            headers={"Authorization": "Bearer admin-token"},
        )
        assert ok_delete.status_code == 200, ok_delete.text
        ok_retention = await c.post(
            retention_path,
            json={"retention_days": 1},
            headers={"Authorization": "Bearer admin-token"},
        )
        assert ok_retention.status_code == 200, ok_retention.text
        assert gateway.config.projects["test-project"].replay.retention_days == 1
