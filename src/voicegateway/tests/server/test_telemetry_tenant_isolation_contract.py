"""Wave 0: replay the cross-tenant fixtures against a real app.

The file is split into three labelled sections, one per fixture kind, because
they mean different things and a reader must not confuse them:

- **Guarantees** assert behavior production already has. Ordinary passing
  tests. If one goes red, something regressed.
- **Characterizations** assert, twice, what production does today and what it
  must do instead. The "today" assertion is labelled as documenting a defect
  so nobody mistakes a green run for a safe system. The "must" assertion
  carries ``xfail(strict=True)``: it fails now, and the day the fix lands it
  XPASSes, which strict turns into a failure so the implementer has to delete
  the marker deliberately rather than leaving a dead test behind.
- **Absence guards** assert the surfaces a planned rule would need do not
  exist yet.

Everything is driven from ``tests/fixtures/security/``. Adding a case there
adds a test here.
"""

from __future__ import annotations

import importlib
import time

import pytest
from sqlalchemy import text

from voicegateway.inference.session.context import reset_tenant_id, set_tenant
from voicegateway.models.request_model import RequestRecord
from voicegateway.tests.fixtures.security._loader import by_kind
from voicegateway.tests.fixtures.security._schema import SecurityFixture
from voicegateway.tests.server._telemetry_harness import _Harness, _make_key

_ACME = "acme"
_BETA = "beta"


def _record(session_id: str, *, cost: float = 0.05) -> RequestRecord:
    return RequestRecord(
        id=f"req-{session_id}",
        timestamp=time.time(),
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


@pytest.fixture
async def harness():
    """App over a fresh SQLite db, seeded with one session per tenant."""
    h = _Harness()
    await h.gateway.storage._ensure_initialized()
    set_tenant(_ACME)
    await h.gateway.storage.log_request(_record("s-acme-1"))
    set_tenant(_BETA)
    await h.gateway.storage.log_request(_record("s-beta-1", cost=1.00))
    reset_tenant_id()
    try:
        yield h
    finally:
        reset_tenant_id()
        h.cleanup()


async def _headers(harness, fixture: SecurityFixture) -> dict[str, str]:
    """Mint the actor's credential, or return no header at all."""
    if fixture.actor.role == "none":
        return {}
    token = await _make_key(
        harness.gateway,
        tenant_id=fixture.actor.tenant_id,
        role=fixture.actor.role,
    )
    return {"Authorization": f"Bearer {token}"}


async def _victim_rows(harness, fixture: SecurityFixture, response) -> int | None:
    """Count rows attributable to the victim tenant.

    Raises on an unrecognised case rather than returning 0, so a new fixture
    cannot silently skip the count and look like it passed.
    """
    if fixture.contract is not None and fixture.contract.victim_rows is None:
        return None

    path = fixture.request.path
    if path == "/v1/ingest/tool-calls":
        async with harness.gateway.storage._conn.session() as session:
            result = await session.execute(
                text("SELECT COUNT(*) FROM tool_calls WHERE tenant_id = :t"),
                {"t": fixture.victim_tenant_id},
            )
            return int(result.scalar_one())
    if path.startswith("/api/sessions") or path.startswith("/v1/sessions"):
        if response.status_code != 200:
            return 0
        body = response.json()
        rows = body if isinstance(body, list) else body.get("sessions", [])
        # Count on tenant_id, which the session rows carry explicitly. An
        # earlier version keyed on a "session_id" field these rows do not
        # have, so every read counted zero and a leak would have passed.
        return sum(
            1
            for row in rows
            if isinstance(row, dict)
            and row.get("tenant_id") == fixture.victim_tenant_id
        )
    raise AssertionError(
        f"{fixture.case_id}: no victim-row counter for {path}. Add one rather "
        "than letting the case pass without counting."
    )


async def _replay(harness, fixture: SecurityFixture, *, path: str | None = None):
    """Issue the fixture's request and return (status_code, victim_rows)."""
    spec = fixture.request
    headers = await _headers(harness, fixture)
    async with harness.client() as client:
        response = await client.request(
            spec.method,
            path or spec.path,
            params=spec.query or None,
            json=spec.json_body,
            headers=headers,
        )
    rows = await _victim_rows(harness, fixture, response)
    return response.status_code, rows


def _cases(kind: str, *, xfail: bool = False):
    """Parametrize over one fixture kind, one param per case."""
    params = []
    for fixture in by_kind(kind):
        marks = ()
        if xfail:
            marks = (
                pytest.mark.xfail(
                    strict=True,
                    reason=(
                        f"{fixture.gap_id} is open: {fixture.title}. When this "
                        "XPASSes the gap is closed. Delete the marker and the "
                        "matching characterization test in the same PR."
                    ),
                ),
            )
        params.append(pytest.param(fixture, id=fixture.case_id, marks=marks))
    return params


# ==========================================================================
# Section 1 — Guarantees. Production already does this. Must stay green.
# ==========================================================================


@pytest.mark.parametrize("fixture", _cases("guarantee"))
async def test_guarantee_holds(harness, fixture: SecurityFixture):
    """A rule production already satisfies. Red here means a regression."""
    status, rows = await _replay(harness, fixture)
    assert status in fixture.contract.status_code, (
        f"{fixture.case_id}: expected one of {fixture.contract.status_code}, "
        f"got {status}"
    )
    if fixture.contract.victim_rows is not None:
        assert rows == fixture.contract.victim_rows


async def test_foreign_session_is_indistinguishable_from_a_missing_one(harness):
    """The 404 rule is only worth anything if both 404s look the same.

    Asserted directly rather than through a fixture because it needs two
    requests compared against each other, not one request checked against an
    expectation.
    """
    fixture = next(
        f for f in by_kind("guarantee") if f.case_id == "session_detail_foreign_id"
    )
    headers = await _headers(harness, fixture)
    async with harness.client() as client:
        foreign = await client.get("/api/sessions/s-beta-1", headers=headers)
        invented = await client.get("/api/sessions/s-does-not-exist", headers=headers)
    assert foreign.status_code == invented.status_code == 404
    assert set(foreign.json()) == set(invented.json()), (
        "a foreign session's error body differs in shape from a missing "
        "one's, which leaks that the id is real"
    )


async def test_v1_mirror_never_serves_the_victim_tenant(harness):
    """The /v1 read surface must not leak, though it refuses differently.

    The two surfaces reach the same outcome by different routes, and the
    difference is worth stating rather than papering over. ``/api/sessions``
    declares a ``tenant`` query param, so a foreign value is a request it
    understands and refuses with 403. ``/v1/sessions`` declares no such param,
    so the same string is an unknown query key: it is ignored, and the read
    stays scoped to the principal. Measured, the /v1 response contains only
    the actor's own session.

    Asserting 403 here would therefore be asserting an implementation detail
    of the /api surface. The property that actually has to hold on both is
    that the victim's rows never appear, so that is what this checks.
    """
    fixture = next(
        f for f in by_kind("guarantee") if f.case_id == "read_tenant_param_override"
    )
    status, rows = await _replay(harness, fixture, path="/v1/sessions")
    assert status in (200, 403)
    assert rows == 0, "the /v1 read surface served the victim tenant's rows"


# ==========================================================================
# Section 2 — Characterizations. These assert TODAY'S WRONG BEHAVIOR.
# A green run here is not a safe system. It is a recorded defect.
# ==========================================================================


@pytest.mark.parametrize("fixture", _cases("characterization"))
async def test_characterize_current_defect(harness, fixture: SecurityFixture):
    """Pins what production does today so the fix has a before to point at."""
    status, rows = await _replay(harness, fixture)
    assert status in fixture.observed.status_code, (
        f"{fixture.case_id}: production no longer behaves as recorded "
        f"(expected {fixture.observed.status_code}, got {status}). If it was "
        "fixed, delete this case's characterization and its xfail together."
    )
    if fixture.observed.victim_rows is not None:
        assert rows == fixture.observed.victim_rows


@pytest.mark.parametrize("fixture", _cases("characterization", xfail=True))
async def test_contract_is_met(harness, fixture: SecurityFixture):
    """The Wave 1 target. Expected to fail until the gap is closed."""
    status, rows = await _replay(harness, fixture)
    assert status in fixture.contract.status_code
    if fixture.contract.victim_rows is not None:
        assert rows == fixture.contract.victim_rows


# ==========================================================================
# Section 3 — Absence guards. "Planned" must be falsifiable.
# ==========================================================================


@pytest.mark.parametrize("fixture", _cases("absence"))
def test_planned_surfaces_do_not_exist_yet(fixture: SecurityFixture):
    """If one of these appears, the case must be rewritten, not deleted."""
    for surface in fixture.absent_surfaces:
        module = importlib.import_module(surface.module)
        owner = getattr(module, surface.attribute)
        fields = getattr(owner, "model_fields", None)
        names = set(fields) if fields else set(getattr(owner, "__annotations__", {}))
        assert surface.field not in names, (
            f"{surface.attribute}.{surface.field} now exists. "
            f"{fixture.gap_id} may be closed: rewrite {fixture.case_id} as a "
            "characterization or a guarantee."
        )
