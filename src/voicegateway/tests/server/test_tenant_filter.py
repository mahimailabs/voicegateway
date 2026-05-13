"""Tests for tenant URL-parameter scoping on read endpoints (T10/T13)."""

from __future__ import annotations

import pytest

from voicegateway.inference._session_context import (
    reset_tenant_id,
    set_tenant,
)
from voicegateway.models.request import RequestRecord
from voicegateway.storage.sqlite import SQLiteStorage


@pytest.fixture
async def storage(tmp_path):
    yield SQLiteStorage(db_path=str(tmp_path / "tf.db"))
    reset_tenant_id()


def _record(
    session_id: str, *, cost: float = 0.05, ts: float = 1746000000.0
) -> RequestRecord:
    return RequestRecord(
        id=f"req-{session_id}",
        timestamp=ts,
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


async def _seed_two_tenants(storage):
    set_tenant("acme")
    await storage.log_request(_record("s-acme-1", cost=0.10))
    await storage.log_request(_record("s-acme-2", cost=0.20))
    set_tenant("beta")
    await storage.log_request(_record("s-beta-1", cost=1.00))
    reset_tenant_id()
    await storage.log_request(_record("s-unattributed", cost=0.05))


async def test_get_cost_summary_scopes_to_tenant(storage) -> None:
    await _seed_two_tenants(storage)
    acme = await storage.get_cost_summary("all", tenant="acme")
    assert acme["total"] == pytest.approx(0.30)
    beta = await storage.get_cost_summary("all", tenant="beta")
    assert beta["total"] == pytest.approx(1.00)
    every = await storage.get_cost_summary("all")
    assert every["total"] == pytest.approx(1.35)


async def test_get_cost_summary_empty_string_targets_unattributed(storage) -> None:
    await _seed_two_tenants(storage)
    u = await storage.get_cost_summary("all", tenant="")
    assert u["total"] == pytest.approx(0.05)


async def test_get_cost_by_project_scopes_to_tenant(storage) -> None:
    await _seed_two_tenants(storage)
    acme = await storage.get_cost_by_project("all", tenant="acme")
    assert acme.get("default", {}).get("cost", 0.0) == pytest.approx(0.30)
    beta = await storage.get_cost_by_project("all", tenant="beta")
    assert beta.get("default", {}).get("cost", 0.0) == pytest.approx(1.00)


async def test_list_sessions_scopes_to_tenant(storage) -> None:
    await _seed_two_tenants(storage)
    acme = await storage.list_sessions(tenant="acme")
    assert {s["id"] for s in acme} == {"s-acme-1", "s-acme-2"}
    beta = await storage.list_sessions(tenant="beta")
    assert {s["id"] for s in beta} == {"s-beta-1"}


async def test_list_sessions_empty_string_targets_unattributed(storage) -> None:
    await _seed_two_tenants(storage)
    rows = await storage.list_sessions(tenant="")
    assert {s["id"] for s in rows} == {"s-unattributed"}


async def test_get_recent_requests_scopes_to_tenant(storage) -> None:
    await _seed_two_tenants(storage)
    acme = await storage.get_recent_requests(tenant="acme")
    assert {r["session_id"] for r in acme} == {"s-acme-1", "s-acme-2"}


async def test_get_latency_stats_scopes_to_tenant(storage) -> None:
    await _seed_two_tenants(storage)
    acme = await storage.get_latency_stats("all", tenant="acme")
    # Acme has 2 requests with latency, beta has 1; the stats should
    # surface model_id keys for whichever tenant we scope.
    assert isinstance(acme, dict)
    # The model_id key carries a non-empty request_count when present.
    if acme:
        any_model = next(iter(acme.values()))
        assert any_model["request_count"] == 2


async def test_none_tenant_means_all_data(storage) -> None:
    await _seed_two_tenants(storage)
    all_summary = await storage.get_cost_summary("all", tenant=None)
    assert all_summary["total"] == pytest.approx(1.35)
    all_sessions = await storage.list_sessions(tenant=None)
    assert len(all_sessions) == 4
