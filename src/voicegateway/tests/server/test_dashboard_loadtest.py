"""``GET /api/loadtest/runs``: the read the dashboard's load-run panel makes.

Two properties carry this endpoint.

**It is a READ, so it lives behind ``require_principal`` on the dashboard
router.** Hanging it off the ``/v1/calls`` router would demand a write scope to
look at a table, because that router declares ``require_scope("write")`` on
itself.

**Provenance is derived, not stored.** A run is measured only when it holds an
artifact checksum, so a reader cannot be shown a synthetic run wearing a
measured badge.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.repository.load_runs_repository import (
    LoadRunInput,
    LoadRunTestInput,
)
from voicegateway.server.main import build_app

NOW = 1_785_600_000_000


@pytest.fixture
def gw(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "loadtest.db"))
    return Gateway(config_path=temp_config)


@pytest.fixture
async def client(gw):
    app = build_app(gw, enable_mcp_sse=False, enable_dashboard=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed(gw, *, checksum: str | None) -> None:
    await gw.storage.upsert_load_run(
        LoadRunInput(
            id="ramp-500",
            created_at_ms=NOW,
            label="baseline",
            tool="gossipper",
            artifact_sha256=checksum,
        )
    )
    await gw.storage.upsert_load_run_test(
        LoadRunTestInput(
            run_id="ramp-500",
            name="ramp-500",
            created_at_ms=NOW,
            peak_concurrency=492,
            attempted_calls=15000,
            succeeded_calls=14985,
            failed_calls=15,
            failed_timeout=3,
            # A genuine zero, which must survive as 0 and not become null.
            failed_parse_error=0,
        )
    )


async def test_a_run_without_a_checksum_is_reported_synthetic(gw, client) -> None:
    await _seed(gw, checksum=None)
    body = (await client.get("/api/loadtest/runs")).json()
    [run] = body["runs"]
    assert run["data_provenance"] == "synthetic"


async def test_a_run_with_a_checksum_is_reported_measured(gw, client) -> None:
    await _seed(gw, checksum="a" * 64)
    body = (await client.get("/api/loadtest/runs")).json()
    assert body["runs"][0]["data_provenance"] == "measured"


async def test_tests_are_embedded_so_one_pathname_serves_the_page(gw, client) -> None:
    """The demo build answers by pathname only, with the query string stripped.

    A per-run path could not be fixtured, so the page would throw in demo mode.
    Embedding keeps this to a single fixturable request.
    """
    await _seed(gw, checksum=None)
    [run] = (await client.get("/api/loadtest/runs")).json()["runs"]
    assert [t["name"] for t in run["tests"]] == ["ramp-500"]
    assert run["tests"][0]["peak_concurrency"] == 492


async def test_a_genuine_zero_survives_beside_the_nulls(gw, client) -> None:
    """0 and null are different claims and both must reach the browser."""
    await _seed(gw, checksum=None)
    [test] = (await client.get("/api/loadtest/runs")).json()["runs"][0]["tests"]
    assert test["failed_parse_error"] == 0
    assert test["failed_cancelled"] is None
    assert test["peak_cpu_utilisation"] is None


async def test_no_runs_is_an_empty_list_not_an_error(gw, client) -> None:
    resp = await client.get("/api/loadtest/runs")
    assert resp.status_code == 200
    assert resp.json()["runs"] == []


async def test_storage_disabled_is_503_not_an_empty_list(gw, monkeypatch) -> None:
    """ "Nothing imported" and "this deployment records nothing" differ."""
    # storage is a read-only property, so the absence is simulated at the
    # property rather than by assigning through it.
    monkeypatch.setattr(type(gw), "storage", property(lambda self: None))
    app = build_app(gw, enable_mcp_sse=False, enable_dashboard=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/loadtest/runs")
    assert resp.status_code == 503
    assert "not an empty list" in resp.json()["detail"]


async def test_a_read_key_can_reach_it_without_a_write_scope(gw) -> None:
    """The claim behind putting this on the dashboard router.

    Asserted behaviourally rather than by introspecting which router object
    holds the route: FastAPI changes what ``router.routes`` contains between
    versions (a newer one wraps included routers in an object with no ``path``),
    so a structural check passes locally and breaks in CI on a different
    resolved version. What actually matters is that a caller holding only a read
    scope can fetch this, which is what a write-scoped router would have denied.
    """
    from voicegateway.core.auth import ApiKey
    from voicegateway.server.api._deps import READ_SCOPE

    app = build_app(gw, enable_mcp_sse=False, enable_dashboard=True)
    app.state.api_keys = [
        ApiKey(token="read-only-token", name="viewer", scopes=(READ_SCOPE,))
    ]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        allowed = await c.get(
            "/api/loadtest/runs",
            headers={"Authorization": "Bearer read-only-token"},
        )
        # Non-vacuous: auth only enforces once keys are configured, so without
        # these two the 200 above would prove nothing but that auth was off.
        anonymous = await c.get("/api/loadtest/runs")
        wrong = await c.get(
            "/api/loadtest/runs", headers={"Authorization": "Bearer wrong-token"}
        )
    assert allowed.status_code == 200, allowed.text
    assert anonymous.status_code == 401
    assert wrong.status_code == 401


async def test_the_limit_is_bounded(gw, client) -> None:
    """The cap bounds the QUERY COUNT: one read per run for its tests."""
    resp = await client.get("/api/loadtest/runs?limit=100000")
    assert resp.status_code == 422
