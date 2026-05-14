"""Tests for tenant_id stamping at session-create (REQ-VG-TENANT-001)."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from voicegateway.inference.session.context import (
    reset_tenant_id,
    set_tenant,
)
from voicegateway.models.request_model import RequestRecord
from voicegateway.services.storage_service import StorageService


@pytest.fixture
async def storage(tmp_path):
    yield StorageService(db_path=str(tmp_path / "sct.db"))
    reset_tenant_id()


def _record(
    session_id: str, *, cost: float = 0.05, ts: float = 1746000000.0
) -> RequestRecord:
    return RequestRecord(
        id=f"req-{session_id}-{ts}-{cost}",
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


async def _fetch_tenant(
    storage: StorageService, table: str, key: str, value: str
) -> str | None:
    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        result = await db.execute(
            text(f"SELECT tenant_id FROM {table} WHERE {key} = :value"),
            {"value": value},
        )
        row = result.first()
    assert row is not None
    return row[0]


async def test_caller_with_tenant_tags_session(storage) -> None:
    set_tenant("acme")
    await storage.log_request(_record("s1"))

    tenant = await _fetch_tenant(storage, "sessions", "id", "s1")
    assert tenant == "acme"


async def test_caller_with_no_tenant_writes_null(storage) -> None:
    reset_tenant_id()  # belt-and-suspenders; the fixture also resets on teardown
    await storage.log_request(_record("s2"))

    tenant = await _fetch_tenant(storage, "sessions", "id", "s2")
    assert tenant is None


async def test_requests_row_also_carries_tenant(storage) -> None:
    """T08: tenant_id propagates to the requests row as well."""
    set_tenant("acme")
    await storage.log_request(_record("s3"))

    tenant = await _fetch_tenant(storage, "requests", "session_id", "s3")
    assert tenant == "acme"


async def test_coalesce_keeps_first_tenant_on_conflict(storage) -> None:
    """A second request without a scope cannot blank an already-tagged row."""
    set_tenant("acme")
    await storage.log_request(_record("s4"))

    reset_tenant_id()
    await storage.log_request(_record("s4", cost=0.10))

    tenant = await _fetch_tenant(storage, "sessions", "id", "s4")
    assert tenant == "acme"


async def test_deferred_attribution_fills_null_slot(storage) -> None:
    """A first unattributed request followed by a scoped one tags the row."""
    reset_tenant_id()
    await storage.log_request(_record("s5"))

    set_tenant("acme")
    await storage.log_request(_record("s5", cost=0.10))

    tenant = await _fetch_tenant(storage, "sessions", "id", "s5")
    assert tenant == "acme"


async def test_attach_session_sets_tenant_via_ctx_var(storage) -> None:
    """attach_session(tenant_id=...) flows through to the next log_request."""
    from voicegateway.inference.session.attach import attach_session

    class FakeAgent:
        def __init__(self) -> None:
            self.handlers: dict = {}

        def on(self, name, handler):
            self.handlers[name] = handler

    fake = FakeAgent()
    attach_session(fake, session_id="s6", tenant_id="beta")
    await storage.log_request(_record("s6"))

    tenant = await _fetch_tenant(storage, "sessions", "id", "s6")
    assert tenant == "beta"
