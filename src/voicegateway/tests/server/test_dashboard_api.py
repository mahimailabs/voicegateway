"""Tests for the dashboard ``/api/*`` endpoints.

All dashboard handlers now live under
:mod:`voicegateway.server.api.dashboard` and are reached through the
daemon's ``build_app()`` (the standalone ``dashboard.api.main``
FastAPI app was deleted in the cut-over commit). The ``client``
fixture builds the daemon app once per test.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.server.main import build_app


@pytest.fixture
def gateway(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "dash-test.db"))
    return Gateway(config_path=temp_config)


@pytest.fixture
async def client(gateway):
    app = build_app(gateway, enable_mcp_sse=False, enable_dashboard=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_api_status(client):
    resp = await client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "providers" in data
    assert "models" in data
    assert "fallbacks" in data


async def test_api_status_includes_pricing_freshness(client):
    """Item C: dashboard /api/status mirrors the /v1/status pricing"""
    resp = await client.get("/api/status")
    data = resp.json()
    assert "pricing" in data
    assert data["pricing"]["llm"]["source"].startswith("genai-prices@")
    for modality in ("stt", "tts"):
        entry = data["pricing"][modality]
        assert entry["source"].startswith("voicegateway-catalog@")
        # ISO YYYY-MM-DD shape
        assert len(entry["oldest_entry_date"].split("-")) == 3


async def test_api_costs(client):
    resp = await client.get("/api/costs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0.0
    assert "by_provider" in data


async def test_api_costs_with_project(client):
    resp = await client.get("/api/costs?project=test-project")
    assert resp.status_code == 200


async def test_api_costs_includes_pricing_source(client, gateway):
    """Q7: dashboard's /api/costs always includes per-row"""
    import time
    import uuid

    from voicegateway.models.request_model import RequestRecord

    await gateway.storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=time.time(),
            modality="stt",
            model_id="deepgram/nova-3",
            provider="deepgram",
            project="test-project",
            input_units=1.0,
            cost_usd=0.0043,
            pricing_source="voicegateway-catalog@2026-05-04",
        )
    )

    resp = await client.get("/api/costs?period=today")
    data = resp.json()
    entry = data["by_model"]["deepgram/nova-3"]
    assert entry["pricing_source"] == "voicegateway-catalog@2026-05-04"


# ---------------------------------------------------------------------------
# /api/sessions and /api/sessions/{id} (Item A: dashboard mirror of /v1)
# ---------------------------------------------------------------------------


async def _seed_session_request(
    storage,
    sid: str,
    *,
    modality: str = "stt",
    cost: float = 0.001,
    ts: float | None = None,
    project: str = "test-project",
    provider: str = "deepgram",
) -> None:
    import time
    import uuid

    from voicegateway.models.request_model import RequestRecord

    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=ts if ts is not None else time.time(),
            modality=modality,
            model_id=f"{provider}/{modality}-test",
            provider=provider,
            project=project,
            cost_usd=cost,
            session_id=sid,
        )
    )


async def test_api_sessions_returns_seeded_rows(client, gateway):
    await _seed_session_request(gateway.storage, "vg-dash-a", cost=0.01)
    await _seed_session_request(gateway.storage, "vg-dash-b", cost=0.02)

    resp = await client.get("/api/sessions")
    assert resp.status_code == 200
    rows = resp.json()
    ids = {r["id"] for r in rows}
    assert {"vg-dash-a", "vg-dash-b"}.issubset(ids)


async def test_api_sessions_orders_by_cost(client, gateway):
    """Mirror endpoint must honour the same order_by whitelist as"""
    await _seed_session_request(
        gateway.storage, "vg-cheap", cost=0.001, ts=1700000300.0
    )
    await _seed_session_request(gateway.storage, "vg-mid", cost=0.05, ts=1700000200.0)
    await _seed_session_request(
        gateway.storage, "vg-pricey", cost=0.50, ts=1700000100.0
    )

    resp = await client.get("/api/sessions?order_by=cost_desc")
    assert resp.status_code == 200
    rows = resp.json()
    assert [r["id"] for r in rows] == ["vg-pricey", "vg-mid", "vg-cheap"]


async def test_api_sessions_invalid_order_by_returns_422(client):
    resp = await client.get("/api/sessions?order_by=cost_random")
    assert resp.status_code == 422


async def test_api_sessions_project_filter(client, gateway):
    await _seed_session_request(gateway.storage, "vg-prod", project="test-project")
    await _seed_session_request(gateway.storage, "vg-other", project="default")

    resp = await client.get("/api/sessions?project=test-project")
    rows = resp.json()
    ids = {r["id"] for r in rows}
    assert "vg-prod" in ids
    assert "vg-other" not in ids


async def test_api_session_detail_carries_breakdown(client, gateway):
    """The detail endpoint must return the per-modality breakdown +"""
    await _seed_session_request(
        gateway.storage,
        "vg-detail",
        modality="stt",
        cost=0.01,
        provider="deepgram",
    )
    await _seed_session_request(
        gateway.storage,
        "vg-detail",
        modality="llm",
        cost=0.05,
        provider="openai",
    )
    await _seed_session_request(
        gateway.storage,
        "vg-detail",
        modality="tts",
        cost=0.04,
        provider="cartesia",
    )

    resp = await client.get("/api/sessions/vg-detail")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "vg-detail"
    assert set(data["by_modality"].keys()) == {"stt", "llm", "tts"}
    assert data["providers"] == ["cartesia", "deepgram", "openai"]


async def test_api_session_detail_404_for_missing(client):
    resp = await client.get("/api/sessions/no-such-session")
    assert resp.status_code == 404
    assert "no-such-session" in resp.json()["detail"]


async def test_api_sessions_returns_empty_when_storage_disabled(
    temp_config, tmp_path, monkeypatch
):
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
    monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)

    from voicegateway.core.gateway import Gateway

    gw = Gateway(config_path=str(cfg_path))
    assert gw.storage is None
    app = build_app(gw, enable_mcp_sse=False, enable_dashboard=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/sessions")
        assert resp.status_code == 200
        assert resp.json() == []

        resp = await c.get("/api/sessions/anything")
        assert resp.status_code == 404


async def test_api_latency(client):
    resp = await client.get("/api/latency")
    assert resp.status_code == 200


async def test_api_logs(client):
    resp = await client.get("/api/logs")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_api_logs_with_modality(client):
    resp = await client.get("/api/logs?modality=stt")
    assert resp.status_code == 200


async def test_api_overview(client):
    resp = await client.get("/api/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_requests" in data
    assert "active_models" in data
    assert "providers_configured" in data


async def test_api_overview_with_project(client):
    resp = await client.get("/api/overview?project=test-project")
    assert resp.status_code == 200


async def test_api_projects(client):
    resp = await client.get("/api/projects")
    assert resp.status_code == 200
    data = resp.json()
    assert "projects" in data
    assert "stats" in data


async def test_api_projects_includes_source_field(client):
    """Item D: every project carries a ``source`` field so the"""
    resp = await client.get("/api/projects")
    data = resp.json()
    for p in data["projects"]:
        assert "source" in p
        assert p["source"] in {"yaml", "db", "auto"}


async def test_api_guardrails_mirror_policy_and_aggregate(client, gateway):
    from voicegateway.repository import guardrail_events_repository as guardrail_events

    resp = await client.post(
        "/api/projects/test-project/guardrails",
        json={"enabled": True, "categories": {"pii": "redact"}},
    )
    assert resp.status_code == 200
    assert resp.json()["policy"]["categories"]["pii"] == "redact"

    await _seed_session_request(gateway.storage, "vg-guard", project="test-project")
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        await guardrail_events.create_event(
            db,
            session_id="vg-guard",
            event_type="fired",
            category="pii",
            action="redact",
            context_excerpt="phone",
        )
        await db.commit()

    resp = await client.get("/api/guardrails/aggregate?category=pii")
    assert resp.status_code == 200
    data = resp.json()
    assert data["counts"] == [{"category": "pii", "action": "redact", "count": 1}]
    assert data["top_sessions"][0]["session_id"] == "vg-guard"


async def test_missing_frontend_fallback(client):
    """When the Vite build doesn't exist, root returns a helpful error."""
    resp = await client.get("/")
    # Either serves index.html (if dist exists) or returns the error JSON
    assert resp.status_code == 200
