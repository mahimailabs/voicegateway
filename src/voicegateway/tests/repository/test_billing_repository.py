"""Billable-usage aggregation: rated revenue, cost, and margin per tenant.

Seeds real rated rows through CostTracker + the tenant context var, then
rolls them up through StorageService.get_billable_usage / line items.
"""

from __future__ import annotations

import pytest

from voicegateway.billing.rate_card import RateCard, RateRule
from voicegateway.inference.session.context import reset_tenant_id, set_tenant
from voicegateway.middleware.cost_tracker_middleware import CostTracker
from voicegateway.services.storage_service import StorageService


async def _seed(tmp_path) -> StorageService:
    storage = StorageService(str(tmp_path / "billing.db"))
    tracker = CostTracker(
        storage,
        rate_card=RateCard(rules=[RateRule(provider="deepgram", markup=1.5)]),
    )

    async def write(tenant: str, n: int) -> None:
        set_tenant(tenant)
        try:
            for _ in range(n):
                rec = tracker.create_record(
                    model_id="deepgram/nova-3",
                    modality="stt",
                    provider="deepgram",
                    input_units=1.0,  # cost 0.0048, rated 0.0072
                )
                await tracker.log_request(rec)
        finally:
            reset_tenant_id()

    await write("acme", 2)
    await write("globex", 1)
    return storage


async def test_billable_usage_rolls_up_per_tenant(tmp_path) -> None:
    storage = await _seed(tmp_path)
    try:
        rows = await storage.get_billable_usage(period="today")
    finally:
        await storage.aclose()

    by_tenant = {r["tenant_id"]: r for r in rows}
    assert set(by_tenant) == {"acme", "globex"}

    acme = by_tenant["acme"]
    assert acme["requests"] == 2
    assert acme["cost_usd"] == pytest.approx(0.0096)  # 2 x 0.0048
    assert acme["rated_usd"] == pytest.approx(0.0144)  # 2 x 0.0072
    assert acme["margin_usd"] == pytest.approx(0.0048)
    assert acme["margin_pct"] == pytest.approx(100.0 / 3.0)  # margin/rated

    globex = by_tenant["globex"]
    assert globex["requests"] == 1
    assert globex["rated_usd"] == pytest.approx(0.0072)

    # Ordered by rated revenue descending: acme (0.0144) before globex.
    assert rows[0]["tenant_id"] == "acme"


async def test_billable_usage_filters_by_tenant(tmp_path) -> None:
    storage = await _seed(tmp_path)
    try:
        rows = await storage.get_billable_usage(period="today", tenant="globex")
    finally:
        await storage.aclose()
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == "globex"
    assert rows[0]["requests"] == 1


async def test_tenant_line_items_break_down_by_model(tmp_path) -> None:
    storage = await _seed(tmp_path)
    try:
        items = await storage.get_tenant_line_items("acme", period="today")
    finally:
        await storage.aclose()
    assert len(items) == 1
    item = items[0]
    assert item["modality"] == "stt"
    assert item["model_id"] == "deepgram/nova-3"
    assert item["requests"] == 2
    assert item["rated_usd"] == pytest.approx(0.0144)
    assert item["margin_usd"] == pytest.approx(0.0048)
