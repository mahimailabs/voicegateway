"""POST /v1/ingest re-rates fleet rows against the collector's own card.

Agents record raw cost and rate at pass-through (no card client-side), so the
collector is the source of truth for margins: it rates each ingested row with
its own rate card, using the tenant resolved from the verified key, and does
not trust an agent-supplied rated price.
"""

from __future__ import annotations

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.repository import api_keys_repository as api_keys
from voicegateway.server import build_app

_CONFIG = {
    "providers": {"openai": {"api_key": "test-key"}},
    "models": {"stt": {}, "llm": {}, "tts": {}},
    "projects": {},
    "fallbacks": {"stt": [], "llm": [], "tts": []},
    "cost_tracking": {"enabled": True},
    "rate_card": {
        "rules": [
            {"provider": "openai", "markup": 1.5},
            {"tenant": "thinco", "provider": "openai", "markup": 1.05},
        ]
    },
}


def _gateway(tmp_path, monkeypatch) -> Gateway:
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "ingest-rating.db"))
    monkeypatch.delenv("VOICEGW_API_KEY", raising=False)
    path = tmp_path / "voicegw.yaml"
    path.write_text(yaml.dump(_CONFIG))
    return Gateway(config_path=str(path))


async def _client(gw: Gateway) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=build_app(gw)), base_url="http://test"
    )


async def _key(gw: Gateway, tenant_id: str | None = None) -> str:
    await gw.storage._ensure_initialized()
    async with gw.storage._conn.session() as db:
        created = await api_keys.create_api_key(db, name="bot", tenant_id=tenant_id)
    return created.plaintext


def _payload(rid: str, **over) -> dict:
    base = {
        "id": rid,
        "timestamp": 1_000_000.0,
        "modality": "llm",
        "model_id": "openai/gpt-4o-mini",
        "provider": "openai",
        "project": "fleet",
        "input_units": 100.0,
        "output_units": 50.0,
        "cost_usd": 0.010,
        "agent_id": "agent-a",
    }
    base.update(over)
    return base


async def test_ingest_rates_unrated_fleet_row(tmp_path, monkeypatch) -> None:
    gw = _gateway(tmp_path, monkeypatch)
    key = await _key(gw, tenant_id="acme")
    client = await _client(gw)
    async with client as c:
        resp = await c.post(
            "/v1/ingest",
            headers={"Authorization": f"Bearer {key}"},
            json=[_payload("r-1")],  # no rated fields -> arrives unrated
        )
    assert resp.status_code == 200

    rows = await gw.storage.get_recent_requests(limit=10)
    assert len(rows) == 1
    row = rows[0]
    assert row["tenant_id"] == "acme"
    assert row["cost_usd"] == pytest.approx(0.010)
    assert row["rated_price_usd"] == pytest.approx(0.015)  # 0.010 x 1.5
    assert row["rate_rule"] == "cost_plus:1.5"


async def test_ingest_applies_tenant_scoped_rule(tmp_path, monkeypatch) -> None:
    """The key's tenant selects the per-tenant rule at re-rate time."""
    gw = _gateway(tmp_path, monkeypatch)
    key = await _key(gw, tenant_id="thinco")
    client = await _client(gw)
    async with client as c:
        resp = await c.post(
            "/v1/ingest",
            headers={"Authorization": f"Bearer {key}"},
            json=[_payload("r-2")],
        )
    assert resp.status_code == 200
    row = (await gw.storage.get_recent_requests(limit=10))[0]
    assert row["tenant_id"] == "thinco"
    assert row["rated_price_usd"] == pytest.approx(0.0105)  # 0.010 x 1.05
    assert row["rate_rule"] == "cost_plus:1.05"


async def test_ingest_overwrites_agent_supplied_rated_price(
    tmp_path, monkeypatch
) -> None:
    """A tampered/pre-set rated price on the payload is replaced by the card."""
    gw = _gateway(tmp_path, monkeypatch)
    key = await _key(gw, tenant_id="acme")
    client = await _client(gw)
    async with client as c:
        resp = await c.post(
            "/v1/ingest",
            headers={"Authorization": f"Bearer {key}"},
            json=[_payload("r-3", rated_price_usd=999.0, rate_rule="cost_plus:99")],
        )
    assert resp.status_code == 200
    row = (await gw.storage.get_recent_requests(limit=10))[0]
    assert row["rated_price_usd"] == pytest.approx(0.015)  # not 999
    assert row["rate_rule"] == "cost_plus:1.5"


async def test_ingest_no_card_is_cost_passthrough(tmp_path, monkeypatch) -> None:
    """With no rate card the collector rates at cost pass-through (default:1)."""
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "nocard.db"))
    monkeypatch.delenv("VOICEGW_API_KEY", raising=False)
    cfg = {**_CONFIG}
    cfg.pop("rate_card")
    path = tmp_path / "voicegw.yaml"
    path.write_text(yaml.dump(cfg))
    gw = Gateway(config_path=str(path))
    key = await _key(gw, tenant_id="acme")
    client = await _client(gw)
    async with client as c:
        resp = await c.post(
            "/v1/ingest",
            headers={"Authorization": f"Bearer {key}"},
            json=[_payload("r-4")],
        )
    assert resp.status_code == 200
    row = (await gw.storage.get_recent_requests(limit=10))[0]
    assert row["rated_price_usd"] == pytest.approx(0.010)  # == cost
    assert row["rate_rule"] == "default:1"
