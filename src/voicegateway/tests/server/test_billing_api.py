"""GET /v1/billing/usage + GET /v1/billing/rate-card."""

from __future__ import annotations

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.inference.session.context import reset_tenant_id, set_tenant
from voicegateway.server import build_app

_CFG = {
    "providers": {"deepgram": {"api_key": "k"}},
    "models": {"stt": {}, "llm": {}, "tts": {}},
    "projects": {},
    "fallbacks": {"stt": [], "llm": [], "tts": []},
    "cost_tracking": {"enabled": True},
    "rate_card": {"rules": [{"provider": "deepgram", "markup": 1.5}]},
}


def _cfg(tmp_path) -> str:
    p = tmp_path / "voicegw.yaml"
    p.write_text(yaml.dump(_CFG))
    return str(p)


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "billing-api.db"))
    monkeypatch.delenv("VOICEGW_API_KEY", raising=False)
    return Gateway(config_path=_cfg(tmp_path))


async def _client(gw: Gateway) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=build_app(gw)), base_url="http://test"
    )


async def _seed(gw: Gateway, tenant: str, n: int) -> None:
    set_tenant(tenant)
    try:
        for _ in range(n):
            rec = gw.cost_tracker.create_record(
                model_id="deepgram/nova-3",
                modality="stt",
                provider="deepgram",
                input_units=1.0,  # cost 0.0048, rated 0.0072
            )
            await gw.storage.log_request(rec)
    finally:
        reset_tenant_id()


async def test_usage_rolls_up_per_tenant(gateway):
    await _seed(gateway, "acme", 2)
    await _seed(gateway, "globex", 1)
    client = await _client(gateway)
    async with client as c:
        resp = await c.get("/v1/billing/usage?period=today")
    assert resp.status_code == 200
    body = resp.json()
    by_tenant = {r["tenant_id"]: r for r in body["tenants"]}
    assert by_tenant["acme"]["rated_usd"] == pytest.approx(0.0144)
    assert by_tenant["acme"]["margin_usd"] == pytest.approx(0.0048)
    assert body["totals"]["rated_usd"] == pytest.approx(0.0216)
    assert body["totals"]["requests"] == 3


async def test_usage_with_tenant_returns_line_items(gateway):
    await _seed(gateway, "acme", 2)
    client = await _client(gateway)
    async with client as c:
        resp = await c.get("/v1/billing/usage?period=today&tenant=acme")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["tenants"]) == 1
    assert body["tenants"][0]["tenant_id"] == "acme"
    assert len(body["line_items"]) == 1
    assert body["line_items"][0]["model_id"] == "deepgram/nova-3"
    assert body["line_items"][0]["rated_usd"] == pytest.approx(0.0144)


async def test_rate_card_endpoint_returns_effective_card(gateway):
    client = await _client(gateway)
    async with client as c:
        resp = await c.get("/v1/billing/rate-card")
    assert resp.status_code == 200
    body = resp.json()
    assert body["default_markup"] == 1.0
    assert len(body["rules"]) == 1
    rule = body["rules"][0]
    assert rule["provider"] == "deepgram"
    assert rule["kind"] == "cost_plus"
    assert rule["markup"] == 1.5
    assert rule["rule"] == "cost_plus:1.5"
