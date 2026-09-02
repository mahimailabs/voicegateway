"""ingest is its own scope. write implies it only during the warn release.

An agent key exists to post telemetry. Before this split it had to carry
`write`, which is also what guards provider, model and project mutation, so
every collector credential in the field could rewrite gateway configuration.
"""

from __future__ import annotations

import logging

import pytest

from voicegateway.core import scopes
from voicegateway.repository.api_keys_repository import VerifiedKey
from voicegateway.tests.server._telemetry_harness import _Harness, _make_key

_TOOL_CALL = [
    {
        "session_id": "s-1",
        "call_id": "c-1",
        "tool_name": "lookup",
        "started_at_ms": 1,
        "duration_ms": 2,
        "outcome": "completed",
    }
]
_PROVIDER = {"provider_id": "ollama-x", "provider_type": "ollama", "api_key": ""}


def _key(scope: str) -> VerifiedKey:
    return VerifiedKey(id=1, tenant_id="acme", name="k", scopes=scope)


def test_write_implies_ingest_only_while_not_enforcing():
    """Existing agent keys keep working through 0.26.0, and stop in 0.27.0."""
    key = _key(scopes.WRITE)
    assert key.has_scope(scopes.INGEST) is True
    assert key.has_scope(scopes.INGEST, enforce=True) is False


def test_ingest_never_implies_write():
    """The whole point: a telemetry key must not reach configuration."""
    key = _key(scopes.INGEST)
    assert key.has_scope(scopes.WRITE) is False
    assert key.has_scope(scopes.WRITE, enforce=True) is False


def test_wildcard_satisfies_nothing_under_enforce():
    key = _key(scopes.WILDCARD)
    assert key.has_scope(scopes.INGEST) is True
    assert key.has_scope(scopes.INGEST, enforce=True) is False


def test_an_exact_scope_holds_under_enforce():
    assert _key(scopes.INGEST).has_scope(scopes.INGEST, enforce=True) is True


@pytest.fixture
def harness():
    h = _Harness()
    try:
        yield h
    finally:
        h.cleanup()


async def test_an_ingest_key_may_post_telemetry_but_not_config(harness):
    token = await _make_key(harness.gateway, tenant_id="acme", scopes="ingest")
    headers = {"Authorization": f"Bearer {token}"}
    async with harness.client() as client:
        telemetry = await client.post(
            "/v1/ingest/tool-calls", json=_TOOL_CALL, headers=headers
        )
        config = await client.post("/v1/providers", json=_PROVIDER, headers=headers)

    assert telemetry.status_code == 200
    assert config.status_code == 403, "an ingest key reached gateway configuration"


async def test_a_write_key_still_ingests_but_is_warned(harness, caplog):
    """Compatibility for 0.26.0, with a per-use nudge naming the key."""
    token = await _make_key(harness.gateway, tenant_id="acme", scopes="write")
    headers = {"Authorization": f"Bearer {token}"}
    with caplog.at_level(logging.WARNING):
        async with harness.client() as client:
            response = await client.post(
                "/v1/ingest/tool-calls", json=_TOOL_CALL, headers=headers
            )

    assert response.status_code == 200
    assert "write scope used for ingest" in caplog.text


async def test_a_read_key_cannot_ingest(harness):
    token = await _make_key(harness.gateway, tenant_id="acme", scopes="read")
    async with harness.client() as client:
        response = await client.post(
            "/v1/ingest/tool-calls",
            json=_TOOL_CALL,
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 403


async def test_the_ingest_principal_carries_the_tenant(harness):
    """The handler writes rows under this, so it must be the key's tenant."""
    from sqlalchemy import text

    token = await _make_key(harness.gateway, tenant_id="acme", scopes="ingest")
    async with harness.client() as client:
        await client.post(
            "/v1/ingest/tool-calls",
            json=_TOOL_CALL,
            headers={"Authorization": f"Bearer {token}"},
        )
    async with harness.gateway.storage.session() as db:
        rows = (
            (await db.execute(text("SELECT tenant_id FROM tool_calls"))).scalars().all()
        )
    assert rows == ["acme"]
