"""Regression tests for /v1/providers/{id}/test and /v1/providers/test"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.server import build_app


@pytest.fixture
def gateway(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "test-endpoint.db"))
    return Gateway(config_path=temp_config)


@pytest.fixture
async def client(gateway):
    transport = ASGITransport(app=build_app(gateway))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def healthy_provider_factory(monkeypatch):
    """Stub create_provider so health_check returns ok without a real"""
    received: dict[str, Any] = {}

    class _Healthy:
        def __init__(self, config: dict[str, Any]) -> None:
            received["config"] = dict(config)

        async def health_check(self) -> bool:
            return True

    def _create(provider_name: str, config: dict[str, Any]) -> _Healthy:
        received["provider_type"] = provider_name
        return _Healthy(config)

    monkeypatch.setattr("voicegateway.core.registry.create_provider", _create)
    return received


# ---------------------------------------------------------------------------
# Composite-id tests (the P1 #1 fix)
# ---------------------------------------------------------------------------


async def test_test_endpoint_resolves_db_managed_composite_id(
    client, gateway, healthy_provider_factory
):
    """Persist a per-project DB row with composite id, hit the test"""
    await client.post(
        "/v1/providers",
        json={
            "provider_id": "tony-pizza:openai",
            "provider_type": "openai",
            "api_key": "sk-tony",
            "project": "tony-pizza",
        },
    )

    resp = await client.post("/v1/providers/tony-pizza:openai/test")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok", data
    # The provider_type that reached the registry must be the row's
    # actual openai, NOT the literal composite id.
    assert healthy_provider_factory["provider_type"] == "openai"
    # The decrypted plaintext key reached the provider's __init__.
    assert healthy_provider_factory["config"]["api_key"] == "sk-tony"


async def test_test_endpoint_resolves_yaml_per_project_composite_id(
    tmp_path, monkeypatch, healthy_provider_factory
):
    """A YAML per-project provider key gets tested via the composite"""
    cfg_path = tmp_path / "voicegw.yaml"
    cfg_path.write_text(
        yaml.dump(
            {
                "providers": {},
                "projects": {
                    "tony-pizza": {
                        "name": "Tony",
                        "providers": {"openai": {"api_key": "yaml-tony-openai"}},
                    }
                },
                "models": {"stt": {}, "llm": {}, "tts": {}},
                "stacks": {},
                "fallbacks": {"stt": [], "llm": [], "tts": []},
                "cost_tracking": {"enabled": True},
                "observability": {"latency_tracking": True},
            }
        )
    )
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "yaml.db"))
    gw = Gateway(config_path=str(cfg_path))
    transport = ASGITransport(app=build_app(gw))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post("/v1/providers/tony-pizza:openai/test")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok", data
    assert healthy_provider_factory["provider_type"] == "openai"
    assert healthy_provider_factory["config"]["api_key"] == "yaml-tony-openai"


async def test_test_endpoint_unknown_id_still_404(client):
    resp = await client.post("/v1/providers/this-does-not-exist/test")
    assert resp.status_code == 404


async def test_test_endpoint_legacy_top_level_id_still_works(
    client, gateway, healthy_provider_factory
):
    """Pre-v0.0.5 callers that test by top-level provider id (e.g.,"""
    # The temp_config fixture already has providers.openai configured.
    resp = await client.post("/v1/providers/openai/test")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Stateless endpoint (the P1 #2 fix)
# ---------------------------------------------------------------------------


async def test_stateless_test_runs_without_persisting(
    client, gateway, healthy_provider_factory
):
    """POST /v1/providers/test takes provider_type + api_key + base_url"""
    pre_rows = await gateway.storage.list_managed_providers()
    pre_ids = {r["provider_id"] for r in pre_rows}

    resp = await client.post(
        "/v1/providers/test",
        json={
            "provider_type": "openai",
            "api_key": "sk-test-stateless",
            "base_url": None,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert healthy_provider_factory["provider_type"] == "openai"
    assert healthy_provider_factory["config"]["api_key"] == "sk-test-stateless"

    # No new rows persisted.
    post_rows = await gateway.storage.list_managed_providers()
    post_ids = {r["provider_id"] for r in post_rows}
    assert pre_ids == post_ids


async def test_stateless_test_unknown_provider_type_returns_failed(client):
    resp = await client.post(
        "/v1/providers/test",
        json={"provider_type": "not-a-provider", "api_key": "k"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "failed"
    assert "Unknown" in data["message"]


async def test_stateless_test_health_check_failure_does_not_crash(client, monkeypatch):
    """A simulated exception during health_check returns failed,"""

    class _Raises:
        def __init__(self, config: dict[str, Any]) -> None:
            self._config = config

        async def health_check(self) -> bool:
            raise RuntimeError("simulated failure")

    monkeypatch.setattr(
        "voicegateway.core.registry.create_provider",
        lambda _name, cfg: _Raises(cfg),
    )

    resp = await client.post(
        "/v1/providers/test",
        json={"provider_type": "openai", "api_key": "sk-broken"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "failed"


async def test_stateless_test_does_not_pollute_provider_list(
    client, gateway, healthy_provider_factory
):
    """Ten repeated stateless tests must not accumulate any sentinel"""
    for _ in range(10):
        await client.post(
            "/v1/providers/test",
            json={"provider_type": "openai", "api_key": "k"},
        )
    rows = await gateway.storage.list_managed_providers()
    sentinel_rows = [r for r in rows if "__test__" in r["provider_id"]]
    assert sentinel_rows == []


# ---------------------------------------------------------------------------
# Health-check timeout passthrough (regression guard)
# ---------------------------------------------------------------------------


async def test_stateless_test_timeout_returns_failed(client, monkeypatch):
    class _Slow:
        def __init__(self, config: dict[str, Any]) -> None:
            self._config = config

        async def health_check(self) -> bool:
            import asyncio

            await asyncio.sleep(20)  # > 10s endpoint timeout
            return True

    monkeypatch.setattr(
        "voicegateway.core.registry.create_provider",
        lambda _name, cfg: _Slow(cfg),
    )

    # The endpoint applies a 10s timeout. Patch
    # asyncio.wait_for to a much shorter timeout so the test stays
    # quick.
    import asyncio as _asyncio

    real_wait_for = _asyncio.wait_for
    monkeypatch.setattr(
        _asyncio,
        "wait_for",
        lambda coro, timeout: real_wait_for(coro, timeout=0.05),
    )

    resp = await client.post(
        "/v1/providers/test",
        json={"provider_type": "openai", "api_key": "k"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "failed"


# ---------------------------------------------------------------------------
# PATCH base_url must not repoint the stored key at an unapproved host
#
# POST /v1/providers/{id}/test builds the provider from the stored row, so a
# PATCH that moves base_url to a new host while keeping the stored key is what
# ships that key to a host the caller chose.
# ---------------------------------------------------------------------------


def _config_with_allowlist(tmp_path, hosts: list[str] | None) -> str:
    """Write a minimal config, optionally with the serve host allowlist."""
    serve: dict[str, Any] = {"host": "127.0.0.1", "port": 8080}
    if hosts is not None:
        serve["provider_base_url_hosts"] = hosts
    cfg_path = tmp_path / "voicegw.yaml"
    cfg_path.write_text(
        yaml.dump(
            {
                "providers": {},
                "models": {"stt": {}, "llm": {}, "tts": {}},
                "stacks": {},
                "fallbacks": {"stt": [], "llm": [], "tts": []},
                "cost_tracking": {"enabled": True},
                "observability": {"latency_tracking": True},
                "serve": serve,
            }
        )
    )
    return str(cfg_path)


@pytest.fixture
def allowlist_client(tmp_path, monkeypatch):
    """Build a client whose serve block carries the given host allowlist."""

    def _build(hosts: list[str] | None):
        monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "allowlist.db"))
        gw = Gateway(config_path=_config_with_allowlist(tmp_path, hosts))
        transport = ASGITransport(app=build_app(gw))
        return gw, AsyncClient(transport=transport, base_url="http://test")

    return _build


async def _create_keyed_provider(client, provider_id: str = "openai-managed") -> None:
    resp = await client.post(
        "/v1/providers",
        json={
            "provider_id": provider_id,
            "provider_type": "openai",
            "api_key": "sk-operator-secret",
        },
    )
    assert resp.status_code == 200, resp.text


async def test_patch_to_new_host_with_stored_key_is_rejected(allowlist_client):
    gw, ctx = allowlist_client(None)
    async with ctx as client:
        await _create_keyed_provider(client)
        resp = await client.patch(
            "/v1/providers/openai-managed",
            json={"base_url": "https://attacker.example.com/v1"},
        )
        assert resp.status_code == 400, resp.text
        detail = resp.json()["detail"]
        assert "attacker.example.com" in detail
        # The error must name the config key the operator has to set.
        assert "serve.provider_base_url_hosts" in detail
        # And nothing may have been persisted.
        row = await gw.storage.get_managed_provider("openai-managed")
        assert row["base_url"] is None


async def test_patch_to_allowlisted_host_with_stored_key_is_permitted(
    allowlist_client,
):
    gw, ctx = allowlist_client(["https://proxy.internal.example.com"])
    async with ctx as client:
        await _create_keyed_provider(client)
        resp = await client.patch(
            "/v1/providers/openai-managed",
            json={"base_url": "https://proxy.internal.example.com/v1"},
        )
        assert resp.status_code == 200, resp.text
        row = await gw.storage.get_managed_provider("openai-managed")
        assert row["base_url"] == "https://proxy.internal.example.com/v1"


async def test_patch_to_new_host_with_fresh_key_needs_no_allowlist(allowlist_client):
    """A caller who supplies the key has nothing to exfiltrate."""
    gw, ctx = allowlist_client(None)
    async with ctx as client:
        await _create_keyed_provider(client)
        resp = await client.patch(
            "/v1/providers/openai-managed",
            json={
                "base_url": "https://byo.example.com/v1",
                "api_key": "sk-caller-owned",
            },
        )
        assert resp.status_code == 200, resp.text
        row = await gw.storage.get_managed_provider("openai-managed")
        assert row["base_url"] == "https://byo.example.com/v1"


async def test_patch_same_host_with_stored_key_still_works(allowlist_client):
    """Port and path edits on the host already stored keep working."""
    gw, ctx = allowlist_client(None)
    async with ctx as client:
        await _create_keyed_provider(client)
        first = await client.patch(
            "/v1/providers/openai-managed",
            json={
                "base_url": "https://proxy.example.com/v1",
                "api_key": "sk-operator-secret",
            },
        )
        assert first.status_code == 200, first.text

        resp = await client.patch(
            "/v1/providers/openai-managed",
            json={"base_url": "https://proxy.example.com:8443/v2"},
        )
        assert resp.status_code == 200, resp.text
        row = await gw.storage.get_managed_provider("openai-managed")
        assert row["base_url"] == "https://proxy.example.com:8443/v2"


async def test_patch_to_vendor_default_host_needs_no_allowlist(allowlist_client):
    """The provider's own default host stays reachable with the allowlist unset."""
    gw, ctx = allowlist_client(None)
    async with ctx as client:
        await _create_keyed_provider(client)
        resp = await client.patch(
            "/v1/providers/openai-managed",
            json={"base_url": "https://api.openai.com/v1"},
        )
        assert resp.status_code == 200, resp.text
        row = await gw.storage.get_managed_provider("openai-managed")
        assert row["base_url"] == "https://api.openai.com/v1"


async def test_patch_that_changes_nothing_still_works(allowlist_client):
    gw, ctx = allowlist_client(None)
    async with ctx as client:
        await _create_keyed_provider(client)
        resp = await client.patch("/v1/providers/openai-managed", json={})
        assert resp.status_code == 200, resp.text
        assert resp.json()["updated"] is True
        row = await gw.storage.get_managed_provider("openai-managed")
        assert row["base_url"] is None


async def test_patch_project_only_with_stored_key_still_works(allowlist_client):
    """A non-base_url field edit must not trip the host guard."""
    gw, ctx = allowlist_client(None)
    async with ctx as client:
        await _create_keyed_provider(client)
        resp = await client.patch(
            "/v1/providers/openai-managed", json={"project": "tony-pizza"}
        )
        assert resp.status_code == 200, resp.text
        row = await gw.storage.get_managed_provider("openai-managed")
        assert row["project"] == "tony-pizza"


# ---------------------------------------------------------------------------
# Suppressed unused-import noise (mocks may move into the file later).
# ---------------------------------------------------------------------------


_ = AsyncMock
_ = MagicMock
