"""Immutable accounting API integration tests."""

from __future__ import annotations

import asyncio

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
        assert report.json()["counts"]["rated"] == 1
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
            "/v1/accounting/usage",
            json=[{**_usage("event-2", "attempt-1"), "component": "research"}],
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


async def test_delayed_unbound_usage_keeps_submitted_revision(
    tmp_path, monkeypatch
) -> None:
    async with await _client(tmp_path, monkeypatch) as client:
        await client.post("/v1/accounting/revisions", json=_revision("sell-1", "0.25"))
        await client.post(
            "/v1/accounting/revisions/selling/activate", json={"revision_id": "sell-1"}
        )
        await client.post("/v1/accounting/revisions", json=_revision("sell-2", "0.50"))
        await client.post(
            "/v1/accounting/revisions/selling/activate",
            json={
                "revision_id": "sell-2",
                "expected_current_revision_id": "sell-1",
            },
        )
        delayed = await client.post("/v1/accounting/usage", json=[_usage()])
        assert delayed.json()["receipts"][0]["outcome"] == "accepted"
        report = (await client.get("/v1/accounting/report")).json()
    assert report["selling_total_usd"] == "0.250000000000"


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
        assert rejected.status_code == 200
        assert rejected.json()["receipts"][0]["code"] == "project_not_authorized"
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


async def test_pricing_mutations_require_admin_scope(tmp_path, monkeypatch) -> None:
    gateway, client = await _gateway_client(tmp_path, monkeypatch)
    async with gateway.storage.session() as session:
        read_only = await create_api_key(
            session,
            name="read-only-operator",
            role="admin",
            scopes="read",
        )
        operator = await create_api_key(
            session,
            name="pricing-operator",
            role="admin",
            scopes="read,admin",
        )
    denied = {"Authorization": f"Bearer {read_only.plaintext}"}
    allowed = {"Authorization": f"Bearer {operator.plaintext}"}
    async with client:
        assert (
            await client.post(
                "/v1/accounting/revisions", json=_revision(), headers=denied
            )
        ).status_code == 403
        assert (
            await client.post(
                "/v1/accounting/revisions/selling/activate",
                json={"revision_id": "missing"},
                headers=denied,
            )
        ).status_code == 403
        assert (
            await client.put(
                "/v1/accounting/ownership",
                json={
                    "project_id": "default",
                    "component": "conversation",
                    "mode": "sdk",
                },
                headers=denied,
            )
        ).status_code == 403
        assert (
            await client.post(
                "/v1/accounting/revisions", json=_revision(), headers=allowed
            )
        ).status_code == 201


async def test_tenant_bound_admin_cannot_select_another_tenant(
    tmp_path, monkeypatch
) -> None:
    gateway, client = await _gateway_client(tmp_path, monkeypatch)
    async with gateway.storage.session() as session:
        bound = await create_api_key(
            session,
            name="bound-operator",
            role="admin",
            scopes="read,admin",
            tenant_id="tenant-a",
        )
        global_operator = await create_api_key(
            session,
            name="global-operator",
            role="admin",
            scopes="read,admin",
        )
    async with client:
        denied = await client.post(
            "/v1/accounting/revisions?tenant=tenant-b",
            json=_revision(),
            headers={"Authorization": f"Bearer {bound.plaintext}"},
        )
        assert denied.status_code == 403
        allowed = await client.post(
            "/v1/accounting/revisions?tenant=tenant-b",
            json=_revision(),
            headers={"Authorization": f"Bearer {global_operator.plaintext}"},
        )
        assert allowed.status_code == 201


async def test_ownership_is_authoritative_with_and_without_binding(
    tmp_path, monkeypatch
) -> None:
    async with await _client(tmp_path, monkeypatch) as client:
        assigned = await client.put(
            "/v1/accounting/ownership",
            json={
                "project_id": "default",
                "component": "conversation",
                "mode": "external",
            },
        )
        assert assigned.status_code == 200
        rejected = await client.post("/v1/accounting/usage", json=[_usage()])
        assert rejected.json()["receipts"][0]["code"] == "ownership_mismatch"

        prepared = await client.post(
            "/v1/accounting/prepare",
            json={
                "project_id": "default",
                "component": "conversation",
                "offering": "provider/model",
            },
        )
        binding = prepared.json()
        assert binding["ownership_mode"] == "external"
        assert "acquisition_revision_id" not in binding
        event = _usage("event-2", "attempt-2")
        event["pricing_binding_id"] = binding["binding_id"]
        event["ownership_mode"] = "sdk"
        event["selling_revision_id"] = "producer-spoof"
        accepted = await client.post("/v1/accounting/usage", json=[event])
        assert accepted.json()["receipts"][0]["outcome"] == "accepted"

        switched = await client.put(
            "/v1/accounting/ownership",
            json={
                "project_id": "default",
                "component": "conversation",
                "mode": "sdk",
            },
        )
        assert switched.status_code == 200
        sdk_event = _usage("event-3", "attempt-3")
        sdk_event["selling_revision_id"] = None
        resumed = await client.post("/v1/accounting/usage", json=[sdk_event])
        assert resumed.json()["receipts"][0]["outcome"] == "accepted"


async def test_mixed_batch_returns_per_record_authorization_outcomes(
    tmp_path, monkeypatch
) -> None:
    gateway, client = await _gateway_client(tmp_path, monkeypatch)
    async with gateway.storage.session() as session:
        key = await create_api_key(
            session,
            name="one-project",
            scopes="read,ingest",
            tenant_id="tenant-a",
            project_ids="allowed",
        )
    allowed = _usage()
    allowed["project_id"] = "allowed"
    forbidden = _usage("event-2", "attempt-2")
    forbidden["project_id"] = "forbidden"
    async with client:
        response = await client.post(
            "/v1/accounting/usage",
            json=[allowed, forbidden],
            headers={"Authorization": f"Bearer {key.plaintext}"},
        )
    assert response.status_code == 200
    assert [item["outcome"] for item in response.json()["receipts"]] == [
        "accepted",
        "rejected",
    ]
    assert response.json()["receipts"][1]["code"] == "project_not_authorized"


async def test_incomplete_amount_is_not_a_headline_total(tmp_path, monkeypatch) -> None:
    incomplete_revision = _revision()
    incomplete_revision["rates"].append(
        {"dimension": "text_output", "unit": "token", "rate": "1"}
    )
    incomplete_revision["unsupported_dimensions"].remove("text_output")
    async with await _client(tmp_path, monkeypatch) as client:
        assert (
            await client.post("/v1/accounting/revisions", json=incomplete_revision)
        ).status_code == 201
        response = await client.post("/v1/accounting/usage", json=[_usage()])
        assert response.json()["receipts"][0]["outcome"] == "accepted"
        report = (await client.get("/v1/accounting/report")).json()
    assert report["selling_total_usd"] == "0"
    assert report["incomplete_selling_total_usd"] == "0.250000000000"
    assert report["counts"]["incomplete"] == 1


async def test_missing_measurement_and_unknown_price_have_distinct_statuses(
    tmp_path, monkeypatch
) -> None:
    missing = _usage("missing-event", "missing-attempt")
    missing["quantities"] = [
        {"dimension": "requests", "value": None, "status": "missing"}
    ]
    unknown = _usage("unknown-event", "unknown-attempt")
    unknown["selling_revision_id"] = "unknown-revision"
    async with await _client(tmp_path, monkeypatch) as client:
        await client.post("/v1/accounting/revisions", json=_revision())
        assert (await client.post("/v1/accounting/usage", json=[missing])).json()[
            "receipts"
        ][0]["outcome"] == "accepted"
        assert (await client.post("/v1/accounting/usage", json=[unknown])).json()[
            "receipts"
        ][0]["outcome"] == "accepted"
        report = (await client.get("/v1/accounting/report")).json()
    assert report["counts"]["incomplete"] == 1
    assert report["counts"]["unrated"] == 1


async def test_acquisition_grouping_requires_operator(tmp_path, monkeypatch) -> None:
    gateway, client = await _gateway_client(tmp_path, monkeypatch)
    async with gateway.storage.session() as session:
        key = await create_api_key(
            session,
            name="tenant-reader",
            scopes="read",
            tenant_id="tenant-a",
        )
    async with client:
        response = await client.get(
            "/v1/accounting/report?group_by=acquisition_revision",
            headers={"Authorization": f"Bearer {key.plaintext}"},
        )
    assert response.status_code == 403
    assert "revision" not in response.text.lower()


async def test_failed_activation_preserves_both_independent_sides(
    tmp_path, monkeypatch
) -> None:
    acquisition = _revision("cost-1", "0.1")
    acquisition["side"] = "acquisition"
    async with await _client(tmp_path, monkeypatch) as client:
        await client.post("/v1/accounting/revisions", json=acquisition)
        await client.post("/v1/accounting/revisions", json=_revision())
        await client.post(
            "/v1/accounting/revisions/acquisition/activate",
            json={"revision_id": "cost-1"},
        )
        await client.post(
            "/v1/accounting/revisions/selling/activate",
            json={"revision_id": "sell-1"},
        )
        failed = await client.post(
            "/v1/accounting/revisions/selling/activate",
            json={"revision_id": "sell-1", "expected_current_revision_id": "stale"},
        )
        assert failed.status_code == 409
        acquisition_read = await client.get(
            "/v1/accounting/revisions/acquisition/cost-1"
        )
        selling_read = await client.get("/v1/accounting/revisions/selling/sell-1")
    assert acquisition_read.json()["active"] is True
    assert selling_read.json()["active"] is True


async def test_concurrent_duplicate_delivery_has_one_billable_row(
    tmp_path, monkeypatch
) -> None:
    async with await _client(tmp_path, monkeypatch) as client:
        await client.post("/v1/accounting/revisions", json=_revision())
        responses = await asyncio.gather(
            *(client.post("/v1/accounting/usage", json=[_usage()]) for _ in range(8))
        )
        outcomes = [response.json()["receipts"][0]["outcome"] for response in responses]
        report = (await client.get("/v1/accounting/report")).json()
    assert outcomes.count("accepted") == 1
    assert outcomes.count("duplicate") == 7
    assert report["records"] == 1


async def test_rebatching_preserves_original_receipts(tmp_path, monkeypatch) -> None:
    async with await _client(tmp_path, monkeypatch) as client:
        await client.post("/v1/accounting/revisions", json=_revision())
        first = await client.post(
            "/v1/accounting/usage",
            json=[_usage("event-1", "attempt-1"), _usage("event-2", "attempt-2")],
        )
        receipts = {
            item["event_id"]: item["receipt_id"] for item in first.json()["receipts"]
        }
        rebatched = await client.post(
            "/v1/accounting/usage",
            json=[_usage("event-2", "attempt-2"), _usage("event-1", "attempt-1")],
        )
    assert all(item["outcome"] == "duplicate" for item in rebatched.json()["receipts"])
    assert {
        item["event_id"]: item["receipt_id"] for item in rebatched.json()["receipts"]
    } == receipts


async def test_non_admin_report_is_tenant_isolated(tmp_path, monkeypatch) -> None:
    gateway, client = await _gateway_client(tmp_path, monkeypatch)
    from voicegateway.accounting.contracts import UsageEnvelope
    from voicegateway.services.accounting_service import AccountingService

    async with gateway.storage.session() as session:
        await AccountingService(session, tenant_id="tenant-a").ingest(
            UsageEnvelope.model_validate(_usage("a-event", "a-attempt"))
        )
    async with gateway.storage.session() as session:
        event = _usage("b-event", "b-attempt")
        event["project_id"] = "project-b"
        await AccountingService(session, tenant_id="tenant-b").ingest(
            UsageEnvelope.model_validate(event)
        )
        key = await create_api_key(
            session, name="tenant-b", scopes="read", tenant_id="tenant-b"
        )
    async with client:
        response = await client.get(
            "/v1/accounting/report",
            headers={"Authorization": f"Bearer {key.plaintext}"},
        )
        foreign = await client.get(
            "/v1/accounting/report?tenant=tenant-a",
            headers={"Authorization": f"Bearer {key.plaintext}"},
        )
    assert response.json()["records"] == 1
    assert foreign.status_code == 403


async def test_dashboard_never_exposes_acquisition_fields(
    tmp_path, monkeypatch
) -> None:
    gateway, client = await _gateway_client(tmp_path, monkeypatch)
    from voicegateway.accounting.contracts import PricingRevisionCreate, UsageEnvelope
    from voicegateway.services.accounting_service import AccountingService

    acquisition = _revision("cost-1", "0.1")
    acquisition["side"] = "acquisition"
    async with gateway.storage.session() as session:
        service = AccountingService(session, tenant_id="tenant-a")
        await service.create_revision(PricingRevisionCreate.model_validate(acquisition))
        await service.create_revision(PricingRevisionCreate.model_validate(_revision()))
        event = _usage()
        event["acquisition_revision_id"] = "cost-1"
        await service.ingest(UsageEnvelope.model_validate(event))
        key = await create_api_key(
            session, name="dashboard-reader", scopes="read", tenant_id="tenant-a"
        )
    async with client:
        response = await client.get(
            "/api/accounting",
            headers={"Authorization": f"Bearer {key.plaintext}"},
        )
    assert response.status_code == 200
    assert "acquisition" not in response.text.lower()
    assert "margin" not in response.text.lower()
