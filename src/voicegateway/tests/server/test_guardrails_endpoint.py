"""Tests for v0.6.0 guardrail HTTP endpoints."""

from __future__ import annotations

import time
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.inference._session_context import (
    get_or_freeze_guardrail_policy_snapshot,
    reset_session_id,
    start_session,
)
from voicegateway.models.request import RequestRecord
from voicegateway.repository import guardrail_events
from voicegateway.server import build_app


@pytest.fixture
def gateway(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "guardrails-api.db"))
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
    project: str = "test-project",
    tenant_id: str | None = None,
) -> None:
    from voicegateway.inference._session_context import set_tenant

    if tenant_id is not None:
        set_tenant(tenant_id)
    try:
        await storage.log_request(
            RequestRecord(
                id=str(uuid.uuid4()),
                timestamp=time.time(),
                modality="llm",
                model_id="openai/gpt-test",
                provider="openai",
                project=project,
                cost_usd=0.001,
                session_id=sid,
            )
        )
    finally:
        if tenant_id is not None:
            set_tenant(None)


async def _create_event(storage, **kwargs) -> None:
    db = await storage._ensure_initialized()
    try:
        await guardrail_events.create_event(db, **kwargs)
        await db.commit()
    finally:
        await db.close()


async def test_get_project_guardrails_defaults_disabled(client) -> None:
    resp = await client.get("/v1/projects/test-project/guardrails")

    assert resp.status_code == 200
    data = resp.json()
    assert data["project_id"] == "test-project"
    assert data["policy"]["enabled"] is False
    assert data["policy"]["categories"]["pii"] == "off"
    assert {cat["id"] for cat in data["categories"]} >= {"pii", "medical"}


async def test_post_project_guardrails_validates_and_persists(client) -> None:
    resp = await client.post(
        "/v1/projects/test-project/guardrails",
        json={
            "enabled": True,
            "categories": {"pii": "redact", "prompt_injection": "block"},
        },
    )

    assert resp.status_code == 200
    assert resp.json()["policy"]["categories"]["pii"] == "redact"

    resp = await client.get("/v1/projects/test-project/guardrails")
    data = resp.json()
    assert data["policy"]["enabled"] is True
    assert data["policy"]["categories"]["prompt_injection"] == "block"


async def test_post_project_guardrails_rejects_unknown_action(client) -> None:
    resp = await client.post(
        "/v1/projects/test-project/guardrails",
        json={"enabled": True, "categories": {"pii": "notify"}},
    )

    assert resp.status_code == 400
    assert "pii" in resp.json()["detail"] or "notify" in resp.json()["detail"]


async def test_guardrail_events_and_aggregate_filters(client, gateway) -> None:
    await _seed_session(gateway.storage, "vg-a", tenant_id="tenant-a")
    await _seed_session(gateway.storage, "vg-b", tenant_id="tenant-b")
    await _create_event(
        gateway.storage,
        session_id="vg-a",
        tenant_id="tenant-a",
        event_type="fired",
        category="pii",
        action="redact",
        context_excerpt="phone",
    )
    await _create_event(
        gateway.storage,
        session_id="vg-b",
        tenant_id="tenant-b",
        event_type="fired",
        category="pii",
        action="block",
        context_excerpt="ssn",
    )
    await _create_event(
        gateway.storage,
        session_id="vg-b",
        tenant_id="tenant-b",
        event_type="bypassed",
        context_excerpt="bypass",
    )

    events = await client.get("/v1/guardrails/events?tenant=tenant-b")
    assert events.status_code == 200
    assert [row["event_type"] for row in events.json()["events"]] == [
        "bypassed",
        "fired",
    ]

    aggregate = await client.get("/v1/guardrails/aggregate?category=pii")
    assert aggregate.status_code == 200
    data = aggregate.json()
    assert {
        (row["category"], row["action"], row["count"]) for row in data["counts"]
    } == {
        ("pii", "block", 1),
        ("pii", "redact", 1),
    }
    assert data["top_sessions"][0]["session_id"] in {"vg-a", "vg-b"}


async def test_session_detail_includes_guardrail_flags_and_events(
    client, gateway
) -> None:
    sid = start_session()
    try:
        get_or_freeze_guardrail_policy_snapshot(
            {"enabled": True, "categories": {"medical": "alert"}}
        )
        await _seed_session(gateway.storage, sid)
        await _create_event(
            gateway.storage,
            session_id=sid,
            event_type="fired",
            category="medical",
            action="alert",
            context_excerpt="prescription",
        )

        resp = await client.get(f"/v1/sessions/{sid}")
    finally:
        reset_session_id()

    assert resp.status_code == 200
    data = resp.json()
    assert data["guardrails_active"] is True
    assert data["guardrails_bypassed"] is False
    assert data["guardrail_events"][0]["category"] == "medical"


async def test_guardrail_event_filter_rejects_unknown_category(client) -> None:
    resp = await client.get("/v1/guardrails/aggregate?category=unknown")

    assert resp.status_code == 400
    assert "unknown guardrail category" in resp.json()["detail"]
