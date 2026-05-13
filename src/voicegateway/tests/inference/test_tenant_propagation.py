"""End-to-end tenant propagation across three synthetic sessions.

REQ-VG-TENANT-001 AC-1: every cost row, metric row, and replay event
for a session is tagged with that session's tenant. This test runs
three sessions with three different tenants through
``log_request`` and asserts the resulting aggregates match the
per-tenant ground truth.
"""

from __future__ import annotations

import pytest

from voicegateway.inference._session_context import (
    current_tenant,
    reset_tenant_id,
    set_tenant,
)
from voicegateway.models.request import RequestRecord
from voicegateway.repository import tenants
from voicegateway.storage.sqlite import SQLiteStorage


@pytest.fixture
async def storage(tmp_path):
    yield SQLiteStorage(db_path=str(tmp_path / "prop.db"))
    reset_tenant_id()


def _req(session_id: str, *, cost: float, ts: float = 1746000000.0) -> RequestRecord:
    return RequestRecord(
        id=f"req-{session_id}-{ts}",
        timestamp=ts,
        project="default",
        modality="stt",
        model_id="deepgram/nova-3",
        provider="deepgram",
        input_units=0,
        output_units=0,
        cost_usd=cost,
        pricing_source="test",
        ttfb_ms=None,
        total_latency_ms=None,
        status="success",
        fallback_from=None,
        error_message=None,
        metadata=None,
        session_id=session_id,
    )


async def test_three_tenants_aggregate_independently(storage) -> None:
    # Tenant A: 2 sessions, 3 requests, $0.30 total.
    set_tenant("alpha")
    await storage.log_request(_req("a-1", cost=0.10))
    await storage.log_request(_req("a-1", cost=0.05, ts=1746000010.0))
    await storage.log_request(_req("a-2", cost=0.15))

    # Tenant B: 1 session, 1 request, $1.00 total.
    set_tenant("bravo")
    await storage.log_request(_req("b-1", cost=1.00))

    # Tenant C: 1 session, 2 requests, $0.50 total.
    set_tenant("charlie")
    await storage.log_request(_req("c-1", cost=0.30))
    await storage.log_request(_req("c-1", cost=0.20, ts=1746000020.0))

    reset_tenant_id()

    # Per-tenant cost summary.
    alpha = await storage.get_cost_summary("all", tenant="alpha")
    assert alpha["total"] == pytest.approx(0.30)
    bravo = await storage.get_cost_summary("all", tenant="bravo")
    assert bravo["total"] == pytest.approx(1.00)
    charlie = await storage.get_cost_summary("all", tenant="charlie")
    assert charlie["total"] == pytest.approx(0.50)

    # Tenants repo index aggregates.
    db = await storage._ensure_initialized()
    rows = await tenants.list_tenants(db)
    by_tenant = {r.tenant_id: r for r in rows}
    assert by_tenant["alpha"].session_count == 2
    assert by_tenant["alpha"].total_cost_usd == pytest.approx(0.30)
    assert by_tenant["bravo"].session_count == 1
    assert by_tenant["charlie"].session_count == 1


async def test_context_var_resets_between_tenants(storage) -> None:
    """Switching tenants does not leak across sessions when set_tenant is called."""
    set_tenant("alpha")
    assert current_tenant() == "alpha"

    set_tenant("bravo")
    assert current_tenant() == "bravo"

    reset_tenant_id()
    assert current_tenant() is None


async def test_unattributed_sessions_separate_from_tenant_index(storage) -> None:
    """The list_tenants result excludes NULL-tenant sessions."""
    set_tenant("alpha")
    await storage.log_request(_req("a-1", cost=0.10))

    reset_tenant_id()
    await storage.log_request(_req("u-1", cost=0.05))
    await storage.log_request(_req("u-2", cost=0.05))

    db = await storage._ensure_initialized()
    tenant_rows = await tenants.list_tenants(db)
    assert {r.tenant_id for r in tenant_rows} == {"alpha"}

    unattr = await tenants.get_unattributed_aggregates(db)
    assert unattr.session_count == 2
    assert unattr.total_cost_usd == pytest.approx(0.10)
