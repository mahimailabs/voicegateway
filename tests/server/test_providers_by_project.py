"""Tests for /api/providers/by-project (dashboard) and the project-
aware /v1/providers POST + PATCH (gateway HTTP API).

Wires the v0.0.5 dashboard Providers page (frontend, iter 31-33) to
the backend layer. The dashboard endpoint surfaces both YAML-defined
projects.<id>.providers entries and DB-managed managed_providers rows
with a non-null project column. The /v1/providers POST + PATCH
handlers persist the project field through to managed_providers.
"""

from __future__ import annotations

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.server import build_app

# ---------------------------------------------------------------------------
# Dashboard endpoint: /api/providers/by-project
# ---------------------------------------------------------------------------


@pytest.fixture
def yaml_seeded_gateway(tmp_path, monkeypatch):
    cfg = {
        "providers": {"openai": {"api_key": "global-openai"}},
        "projects": {
            "tony-pizza": {
                "name": "Tony",
                "providers": {
                    "openai": {"api_key": "yaml-tony-openai"},
                    "deepgram": {"api_key": "yaml-tony-dg"},
                },
            },
            "mama-diner": {
                "name": "Mama",
                "providers": {"openai": {"api_key": "yaml-mama-openai"}},
            },
        },
        "default_project": "tony-pizza",
        "models": {"stt": {}, "llm": {}, "tts": {}},
        "stacks": {},
        "fallbacks": {"stt": [], "llm": [], "tts": []},
        "cost_tracking": {"enabled": True},
        "observability": {"latency_tracking": True},
    }
    cfg_path = tmp_path / "voicegw.yaml"
    cfg_path.write_text(yaml.dump(cfg))
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "dash-bp.db"))
    return Gateway(config_path=str(cfg_path))


@pytest.fixture
async def dash_client(yaml_seeded_gateway):
    import dashboard.api.main as dash_module

    dash_module._gateway = yaml_seeded_gateway
    transport = ASGITransport(app=dash_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    dash_module._gateway = None


async def test_by_project_lists_yaml_per_project_keys(dash_client):
    resp = await dash_client.get("/api/providers/by-project")
    assert resp.status_code == 200
    rows = resp.json()["providers"]
    ids = {r["provider_id"] for r in rows}
    assert ids == {
        "tony-pizza:openai",
        "tony-pizza:deepgram",
        "mama-diner:openai",
    }
    sources = {r["source"] for r in rows}
    assert sources == {"yaml"}


async def test_by_project_filter_narrows_to_one_project(dash_client):
    resp = await dash_client.get("/api/providers/by-project?project=mama-diner")
    rows = resp.json()["providers"]
    assert len(rows) == 1
    assert rows[0]["provider_id"] == "mama-diner:openai"


async def test_by_project_masks_api_keys(dash_client):
    resp = await dash_client.get("/api/providers/by-project")
    for row in resp.json()["providers"]:
        assert row["api_key_masked"] is not None
        # The plaintext key must not round-trip in masked form. Each
        # YAML key in the fixture has a "yaml-" prefix; the masked
        # form is "first4...last4" so for short keys the mask can
        # include those chars, but the FULL plaintext must never
        # appear.
        assert row["api_key_masked"] != row.get("_plaintext")


async def test_by_project_includes_db_managed_rows(yaml_seeded_gateway, dash_client):
    """Persist a DB-managed per-project row directly through storage
    and confirm the dashboard endpoint surfaces it tagged source=db.
    """
    storage = yaml_seeded_gateway.storage
    assert storage is not None
    await storage.upsert_managed_provider(
        provider_id="newcomer:openai",
        provider_type="openai",
        api_key="db-newcomer-key",
        project="newcomer",
    )
    await yaml_seeded_gateway.refresh_config()

    resp = await dash_client.get("/api/providers/by-project")
    rows = resp.json()["providers"]
    by_id = {r["provider_id"]: r for r in rows}
    assert "newcomer:openai" in by_id
    assert by_id["newcomer:openai"]["source"] == "db"
    assert by_id["newcomer:openai"]["project"] == "newcomer"


async def test_by_project_excludes_legacy_global_db_rows(
    yaml_seeded_gateway, dash_client
):
    """Rows with project IS NULL belong to the existing /v1/providers
    global view; this dashboard endpoint must not return them.
    """
    storage = yaml_seeded_gateway.storage
    assert storage is not None
    await storage.upsert_managed_provider(
        provider_id="standalone-rider",
        provider_type="openai",
        api_key="legacy-key",
        project=None,
    )
    await yaml_seeded_gateway.refresh_config()

    resp = await dash_client.get("/api/providers/by-project")
    ids = {r["provider_id"] for r in resp.json()["providers"]}
    assert "standalone-rider" not in ids


async def test_by_project_empty_when_no_project_keys(tmp_path, monkeypatch):
    """A gateway with no projects: at all returns an empty list."""
    cfg = {
        "providers": {"openai": {"api_key": "k"}},
        "models": {"stt": {}, "llm": {}, "tts": {}},
        "stacks": {},
        "fallbacks": {"stt": [], "llm": [], "tts": []},
        "cost_tracking": {"enabled": True},
        "observability": {"latency_tracking": True},
    }
    cfg_path = tmp_path / "voicegw.yaml"
    cfg_path.write_text(yaml.dump(cfg))
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "empty.db"))
    gw = Gateway(config_path=str(cfg_path))

    import dashboard.api.main as dash_module

    dash_module._gateway = gw
    transport = ASGITransport(app=dash_module.app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/providers/by-project")
            assert resp.status_code == 200
            assert resp.json() == {"providers": []}
    finally:
        dash_module._gateway = None


# ---------------------------------------------------------------------------
# /v1/providers POST honors the project field
# ---------------------------------------------------------------------------


@pytest.fixture
def gw_for_v1(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "v1-bp.db"))
    return Gateway(config_path=temp_config)


@pytest.fixture
async def v1_client(gw_for_v1):
    transport = ASGITransport(app=build_app(gw_for_v1))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_v1_post_persists_project_field(v1_client, gw_for_v1):
    resp = await v1_client.post(
        "/v1/providers",
        json={
            "provider_id": "tony-pizza:openai",
            "provider_type": "openai",
            "api_key": "sk-tony",
            "project": "tony-pizza",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["project"] == "tony-pizza"

    rows = await gw_for_v1.storage.list_managed_providers()
    saved = next(r for r in rows if r["provider_id"] == "tony-pizza:openai")
    assert saved["project"] == "tony-pizza"


async def test_v1_post_without_project_keeps_legacy_null(v1_client, gw_for_v1):
    """Pre-v0.0.5 callers omit project — column stays NULL."""
    resp = await v1_client.post(
        "/v1/providers",
        json={
            "provider_id": "legacy-openai",
            "provider_type": "openai",
            "api_key": "sk-legacy",
        },
    )
    assert resp.status_code == 200

    rows = await gw_for_v1.storage.list_managed_providers()
    saved = next(r for r in rows if r["provider_id"] == "legacy-openai")
    assert saved["project"] is None


async def test_v1_patch_preserves_project_when_omitted(v1_client, gw_for_v1):
    """Rotating a per-project key must NOT demote it to global scope."""
    await v1_client.post(
        "/v1/providers",
        json={
            "provider_id": "tony-pizza:openai",
            "provider_type": "openai",
            "api_key": "sk-1",
            "project": "tony-pizza",
        },
    )
    resp = await v1_client.patch(
        "/v1/providers/tony-pizza:openai",
        json={"api_key": "sk-rotated"},
    )
    assert resp.status_code == 200

    rows = await gw_for_v1.storage.list_managed_providers()
    saved = next(r for r in rows if r["provider_id"] == "tony-pizza:openai")
    assert saved["project"] == "tony-pizza"


async def test_v1_post_rejects_project_scoped_when_yaml_pins_slot(
    tmp_path, monkeypatch
):
    """If voicegw.yaml already defines projects.<id>.providers.<type>,
    the DB row would silently never be used (ConfigManager keeps the
    YAML entry). The handler must reject the create with 409 instead
    of writing a credential that nobody will read.
    """
    cfg = {
        "providers": {"openai": {"api_key": "global"}},
        "projects": {
            "tony-pizza": {
                "name": "Tony",
                "providers": {"openai": {"api_key": "yaml-tony-openai"}},
            },
        },
        "models": {"stt": {}, "llm": {}, "tts": {}},
        "stacks": {},
        "fallbacks": {"stt": [], "llm": [], "tts": []},
        "cost_tracking": {"enabled": True},
        "observability": {"latency_tracking": True},
    }
    cfg_path = tmp_path / "voicegw.yaml"
    cfg_path.write_text(yaml.dump(cfg))
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "shadow.db"))
    gw = Gateway(config_path=str(cfg_path))
    transport = ASGITransport(app=build_app(gw))

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/providers",
            json={
                "provider_id": "tony-pizza:openai",
                "provider_type": "openai",
                "api_key": "sk-shadowed",
                "project": "tony-pizza",
            },
        )

    assert resp.status_code == 409
    assert "tony-pizza" in resp.json()["detail"]
    assert "openai" in resp.json()["detail"]


async def test_v1_post_allows_project_scoped_when_yaml_slot_empty(
    tmp_path, monkeypatch
):
    """Conversely, when the YAML has the project but not the specific
    provider type, the create must succeed.
    """
    cfg = {
        "providers": {"openai": {"api_key": "global"}},
        "projects": {
            "tony-pizza": {
                "name": "Tony",
                "providers": {"deepgram": {"api_key": "yaml-tony-dg"}},
            },
        },
        "models": {"stt": {}, "llm": {}, "tts": {}},
        "stacks": {},
        "fallbacks": {"stt": [], "llm": [], "tts": []},
        "cost_tracking": {"enabled": True},
        "observability": {"latency_tracking": True},
    }
    cfg_path = tmp_path / "voicegw.yaml"
    cfg_path.write_text(yaml.dump(cfg))
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "no-shadow.db"))
    gw = Gateway(config_path=str(cfg_path))
    transport = ASGITransport(app=build_app(gw))

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/providers",
            json={
                "provider_id": "tony-pizza:openai",
                "provider_type": "openai",
                "api_key": "sk-tony",
                "project": "tony-pizza",
            },
        )

    assert resp.status_code == 200, resp.text


async def test_v1_patch_explicit_project_override_honored(v1_client, gw_for_v1):
    """Explicit project field on PATCH overrides the existing scope —
    rare, but useful for promoting/demoting a row.
    """
    await v1_client.post(
        "/v1/providers",
        json={
            "provider_id": "tony-pizza:openai",
            "provider_type": "openai",
            "api_key": "sk-1",
            "project": "tony-pizza",
        },
    )
    resp = await v1_client.patch(
        "/v1/providers/tony-pizza:openai",
        json={"api_key": "sk-2", "project": "mama-diner"},
    )
    assert resp.status_code == 200

    rows = await gw_for_v1.storage.list_managed_providers()
    saved = next(r for r in rows if r["provider_id"] == "tony-pizza:openai")
    assert saved["project"] == "mama-diner"
