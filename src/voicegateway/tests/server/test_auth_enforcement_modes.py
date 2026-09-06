"""The same request under local_development, warn, and enforce.

Exercised against ``/api/costs``, which already resolves ``require_principal``.
The plan proposed ``/v1/costs``, but that route is still open until Task 12
gates it, so the assertions would have been deferred behind strict xfails and
proved nothing today. A route that is genuinely gated tests the real thing
now, and Task 12 widens the surface rather than switching the test on.
"""

from __future__ import annotations

import logging

import pytest

from voicegateway.server.api._authz import WOULD_REFUSE_EVENT
from voicegateway.tests.server._telemetry_harness import _Harness

_GATED = "/api/costs"


@pytest.fixture
def make_harness():
    made: list[_Harness] = []

    def _make(auth: dict) -> _Harness:
        harness = _Harness(config_overrides={"auth": auth})
        made.append(harness)
        return harness

    yield _make
    for harness in made:
        harness.cleanup()


async def test_no_credential_in_local_development_is_served_silently(
    make_harness, caplog
):
    harness = make_harness({"local_development": True})
    with caplog.at_level(logging.WARNING):
        async with harness.client() as client:
            response = await client.get(_GATED)
    assert response.status_code == 200
    assert WOULD_REFUSE_EVENT not in caplog.text


async def test_no_credential_under_warn_is_served_and_named(make_harness, caplog):
    """0.26.0's default: nothing breaks, but the log says what would."""
    harness = make_harness({"enforcement": "warn"})
    with caplog.at_level(logging.WARNING):
        async with harness.client() as client:
            response = await client.get(_GATED)
    assert response.status_code == 200
    assert WOULD_REFUSE_EVENT in caplog.text
    assert _GATED in caplog.text
    assert harness.app.state.auth_would_refuse >= 1


async def test_no_credential_under_enforce_is_refused(make_harness):
    """VG-SEC-005: absence of a credential no longer means full admin."""
    harness = make_harness({"enforcement": "enforce"})
    async with harness.client() as client:
        response = await client.get(_GATED)
    assert response.status_code == 401
    assert "Authentication required" in response.json()["detail"]


async def test_status_reports_the_mode_and_the_counter(make_harness):
    """The counter is the number an operator checks before flipping to enforce."""
    harness = make_harness({"enforcement": "warn"})
    async with harness.client() as client:
        await client.get(_GATED)
        await client.get(_GATED)
        status = await client.get("/api/status")

    auth = status.json()["auth"]
    assert auth["enforcement"] == "warn"
    assert auth["local_development"] is False
    assert auth["would_refuse_count"] >= 2


async def test_enforce_does_not_refuse_a_valid_key(make_harness):
    """The gate must let the authorised through, or it is just an outage."""
    from voicegateway.tests.server._telemetry_harness import _make_key

    harness = make_harness({"enforcement": "enforce"})
    token = await _make_key(harness.gateway, tenant_id="acme", role="tenant")
    async with harness.client() as client:
        response = await client.get(
            _GATED, headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code == 200


async def test_enforce_leaves_open_by_design_routes_open(make_harness):
    """Liveness must answer an unauthenticated caller even under enforce."""
    harness = make_harness({"enforcement": "enforce"})
    async with harness.client() as client:
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/api/auth-status")).status_code == 200
