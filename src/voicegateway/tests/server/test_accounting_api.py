"""Immutable accounting API integration tests."""

from __future__ import annotations

import yaml
from httpx import ASGITransport, AsyncClient

from voicegateway.accounting.contracts import PricingDimension
from voicegateway.core.gateway import Gateway
from voicegateway.repository.api_keys_repository import create_api_key
from voicegateway.server import build_app


def _revision(revision_id: str = "sell-1", rate: str = "0.25") -> dict:
    return {
        "revision_id": revision_id,
        "side": "selling",
        "scope": {"offering": "provider/model"},
        "rates": [{"dimension": "requests", "unit": "request", "rate": rate}],
        "unsupported_dimensions": [
            item.value
            for item in PricingDimension
            if item is not PricingDimension.REQUESTS
        ],
    }


def _usage(event_id: str = "event-1", attempt_id: str = "attempt-1") -> dict:
    return {
        "event_id": event_id,
        "attempt_id": attempt_id,
        "project_id": "default",
        "session_id": "session-1",
        "component": "conversation",
        "modality": "llm",
        "offering": "provider/model",
        "model_id": "provider/model",
        "producer_id": "sdk-1",
        "ownership_mode": "sdk",
        "selling_revision_id": "sell-1",
        "occurred_at_ns": 10,
        "quantities": [{"dimension": "requests", "value": "1", "status": "measured"}],
    }


async def _client(tmp_path, monkeypatch) -> AsyncClient:
    config = tmp_path / "voicegw.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "providers": {},
                "models": {"stt": {}, "llm": {}, "tts": {}},
                "projects": {},
                "fallbacks": {"stt": [], "llm": [], "tts": []},
                "cost_tracking": {"enabled": True},
            }
        )
    )
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "accounting.db"))
    monkeypatch.delenv("VOICEGW_API_KEY", raising=False)
    return AsyncClient(
        transport=ASGITransport(app=build_app(Gateway(config_path=str(config)))),
        base_url="http://test",
    )


async def _gateway_client(tmp_path, monkeypatch) -> tuple[Gateway, AsyncClient]:
    config = tmp_path / "scoped.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "providers": {},
                "models": {"stt": {}, "llm": {}, "tts": {}},
                "projects": {},
                "fallbacks": {"stt": [], "llm": [], "tts": []},
                "cost_tracking": {"enabled": True},
                "auth": {"enforcement": "enforce"},
            }
        )
    )
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "scoped.db"))
    gateway = Gateway(config_path=str(config))
    return gateway, AsyncClient(
        transport=ASGITransport(app=build_app(gateway)), base_url="http://test"
    )


async def test_revision_idempotency_conflict_and_usage_receipts(
    tmp_path, monkeypatch
) -> None:
    async with await _client(tmp_path, monkeypatch) as client:
        created = await client.post("/v1/accounting/revisions", json=_revision())
        assert created.status_code == 201, created.text
        assert created.json()["created"] is True
        retried = await client.post("/v1/accounting/revisions", json=_revision())
        assert retried.status_code == 201
        assert retried.json()["created"] is False
        conflict = await client.post(
            "/v1/accounting/revisions", json=_revision(rate="0.3")
        )
        assert conflict.status_code == 409

        first = await client.post("/v1/accounting/usage", json=[_usage()])
        assert first.status_code == 200, first.text
        assert first.json()["receipts"][0]["outcome"] == "accepted"
        duplicate = await client.post("/v1/accounting/usage", json=[_usage()])
        assert duplicate.json()["receipts"][0]["outcome"] == "duplicate"

        report = await client.get("/v1/accounting/report")
        assert report.status_code == 200
        assert report.json()["selling_total_usd"] == "0.250000000000"
        assert "acquisition_total_usd" not in report.text


async def test_conflicting_event_and_attempt_are_rejected(
    tmp_path, monkeypatch
) -> None:
    async with await _client(tmp_path, monkeypatch) as client:
        await client.post("/v1/accounting/revisions", json=_revision())
        assert (
            await client.post("/v1/accounting/usage", json=[_usage()])
        ).status_code == 200
        changed = _usage()
        changed["producer_id"] = "sdk-2"
        response = await client.post("/v1/accounting/usage", json=[changed])
        assert response.json()["receipts"][0]["code"] == "identity_conflict"
        second_event = await client.post(
            "/v1/accounting/usage", json=[_usage("event-2", "attempt-1")]
        )
        assert (
            second_event.json()["receipts"][0]["code"]
            == "attempt_or_ownership_conflict"
        )


async def test_prepared_revision_remains_pinned_after_activation_changes(
    tmp_path, monkeypatch
) -> None:
    async with await _client(tmp_path, monkeypatch) as client:
        await client.post("/v1/accounting/revisions", json=_revision("sell-1", "0.25"))
        activated = await client.post(
            "/v1/accounting/revisions/selling/activate", json={"revision_id": "sell-1"}
        )
        assert activated.status_code == 200, activated.text
        prepared = await client.post(
            "/v1/accounting/prepare",
            json={
                "project_id": "default",
                "component": "conversation",
                "offering": "provider/model",
            },
        )
        assert prepared.status_code == 200, prepared.text
        binding = prepared.json()
        assert binding["selling_revision_id"] == "sell-1"

        await client.post("/v1/accounting/revisions", json=_revision("sell-2", "0.50"))
        switched = await client.post(
            "/v1/accounting/revisions/selling/activate",
            json={
                "revision_id": "sell-2",
                "expected_current_revision_id": "sell-1",
            },
        )
        assert switched.status_code == 200
        event = _usage()
        event["pricing_binding_id"] = binding["binding_id"]
        response = await client.post("/v1/accounting/usage", json=[event])
        assert response.json()["receipts"][0]["outcome"] == "accepted"
        report = await client.get("/v1/accounting/report")
        assert report.json()["selling_total_usd"] == "0.250000000000"


async def test_ingest_principal_enforces_project_allowlist(
    tmp_path, monkeypatch
) -> None:
    gateway, client = await _gateway_client(tmp_path, monkeypatch)
    async with gateway.storage.session() as session:
        key = await create_api_key(
            session,
            name="scoped-agent",
            scopes="read,ingest",
            tenant_id="tenant-a",
            project_ids="allowed",
        )
    headers = {"Authorization": f"Bearer {key.plaintext}"}
    async with client:
        allowed = _usage()
        allowed["project_id"] = "allowed"
        accepted = await client.post(
            "/v1/accounting/usage", json=[allowed], headers=headers
        )
        assert accepted.status_code == 200
        forbidden = _usage("event-2", "attempt-2")
        forbidden["project_id"] = "forbidden"
        rejected = await client.post(
            "/v1/accounting/usage", json=[forbidden], headers=headers
        )
        assert rejected.status_code == 403
        assert "tenant" not in rejected.text.lower()


async def test_tenant_key_cannot_read_acquisition_revision(
    tmp_path, monkeypatch
) -> None:
    gateway, client = await _gateway_client(tmp_path, monkeypatch)
    from voicegateway.accounting.contracts import PricingRevisionCreate
    from voicegateway.services.accounting_service import AccountingService

    acquisition = _revision("cost-1", "0.1")
    acquisition["side"] = "acquisition"
    acquisition["scope"]["tenant_id"] = "tenant-a"
    async with gateway.storage.session() as session:
        await AccountingService(session, tenant_id="tenant-a").create_revision(
            PricingRevisionCreate.model_validate(acquisition)
        )
        key = await create_api_key(
            session,
            name="tenant-reader",
            scopes="read,ingest",
            tenant_id="tenant-a",
        )
    async with client:
        response = await client.get(
            "/v1/accounting/revisions/acquisition/cost-1",
            headers={"Authorization": f"Bearer {key.plaintext}"},
        )
        assert response.status_code == 403
        assert "0.1" not in response.text
