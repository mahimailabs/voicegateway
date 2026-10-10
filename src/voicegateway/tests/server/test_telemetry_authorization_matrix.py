"""The authorization matrix must stay bound to the real app.

The matrix is only worth having if it cannot drift. Two checks do that work:

- **bijection** — every live ``APIRoute`` has exactly one row and every row
  names a live route, so adding an endpoint without classifying it fails CI;
- **classification** — the ``auth`` recorded on each row equals what the
  resolved FastAPI dependency graph actually says today, so a row cannot claim
  a route is guarded when it is not.

Together they mean the matrix cannot disagree with routes or auth wiring
without something going red.
"""

from __future__ import annotations

import pytest

from voicegateway.schemas.telemetry.security_schema import (
    ContractStatus,
    RouteAuth,
    load_authorization_matrix,
)
from voicegateway.tests.server._telemetry_harness import (
    _Harness,
    canonical_route_auth,
    live_route_auth,
    normalize_route_key,
    openapi_route_keys,
)

# Snapshot the served route inventory once at collection and share it across
# every test below. The child interpreter that produces it builds the
# application rather than reading the four routers, because dependencies
# attached at ``include_router`` time exist only on the app's routes. See
# canonical_route_auth's docstring for the demonstration.
#
# No test in this suite is known to mutate router wiring: the full suite passes
# with the app built per module. The isolation is belt and braces, kept because
# it is nearly free, not because a specific polluter was identified.
_LIVE_ROUTE_AUTH = canonical_route_auth()


@pytest.fixture(scope="module")
def matrix():
    return load_authorization_matrix()


@pytest.fixture(scope="module")
def live():
    """Return the collection-time canonical route inventory."""
    return _LIVE_ROUTE_AUTH


# --------------------------------------------------------------------------
# Well-formedness
# --------------------------------------------------------------------------


def test_matrix_loads_and_is_non_trivial(matrix):
    """A matrix that failed to load would make every other test vacuous."""
    assert len(matrix.routes) > 50


# --------------------------------------------------------------------------
# Bijection
# --------------------------------------------------------------------------


def test_every_live_route_has_a_matrix_row(matrix, live):
    """Adding a route without classifying it must fail here."""
    missing = sorted(set(live) - matrix.keys())
    assert not missing, (
        f"{len(missing)} route(s) have no matrix row. Run "
        "`.venv/bin/python tools/scripts/gen_authorization_matrix.py` and "
        f"paste the rows it prints: {missing}"
    )


def test_every_matrix_row_names_a_live_route(matrix, live):
    """Deleting a route must delete its row, or the matrix rots."""
    stale = sorted(matrix.keys() - set(live))
    assert not stale, f"{len(stale)} matrix row(s) reference dead routes: {stale}"


def test_inventory_agrees_with_the_openapi_schema(live):
    """Cross-check the route walk against a version-stable public source.

    This is the test that would have caught the Test Coverage failure at its
    cause instead of six confusing symptoms. FastAPI 0.141 replaced the
    included-route copies that ``include_router`` used to leave on the parent
    with a lazy wrapper, so a walk that only recognised ``APIRoute`` collapsed
    an 89-route inventory to 4. The matrix then reported 85 rows as dead
    routes, which reads like the matrix rotted rather than like the walk
    broke.

    The OpenAPI schema survived that change untouched, so comparing the two
    turns the next internal restructuring into one named failure here.
    """
    harness = _Harness()
    try:
        walked = {normalize_route_key(key) for key in live_route_auth(harness.app)}
        schema = openapi_route_keys(harness.app)
    finally:
        harness.cleanup()

    assert walked == schema, (
        "the route walk disagrees with the OpenAPI schema, so "
        "iter_api_routes no longer understands this FastAPI version. "
        f"missing from the walk: {sorted(schema - walked)}; "
        f"walked but absent from the schema: {sorted(walked - schema)}"
    )
    assert {normalize_route_key(key) for key in live} == schema, (
        "the collection-time snapshot disagrees with the schema even though "
        "the walk agrees, so the snapshot is stale or was taken against a "
        "different application"
    )


# --------------------------------------------------------------------------
# Classification conformance
# --------------------------------------------------------------------------


def test_recorded_auth_matches_the_live_dependency_graph(matrix, live):
    """The strongest row-level check: recorded gating equals real gating."""
    wrong = [
        f"{method} {path}: matrix says {rule.auth.value!r}, app says "
        f"{live[(method, path)]!r}"
        for (method, path), rule in matrix.by_key().items()
        if live[(method, path)] != rule.auth.value
    ]
    assert not wrong, wrong


def test_open_rows_are_exactly_the_unauthenticated_routes(matrix, live):
    """Cross-check the same fact from the other direction."""
    open_rows = {r.key for r in matrix.routes if r.auth is RouteAuth.OPEN}
    open_live = {key for key, auth in live.items() if auth == "open"}
    assert open_rows == open_live


def test_open_routes_are_gap_unless_explicitly_open_by_design(matrix):
    """An open route is a finding unless the row argues why it is not."""
    for rule in matrix.routes:
        if rule.auth is not RouteAuth.OPEN:
            continue
        if rule.status is ContractStatus.ENFORCED:
            assert not rule.tenant_scoped, (
                f"{rule.method} {rule.path}: an open route serving "
                "tenant-scoped data cannot be enforced"
            )
            assert rule.note, (
                f"{rule.method} {rule.path}: an open enforced route must say "
                "why it is open by design"
            )
        else:
            assert rule.gap_id == "VG-SEC-004"


def test_write_scope_no_longer_spans_ingest(matrix):
    """VG-SEC-003 closed: write covers config mutation and nothing else.

    This test is the inverse of the one it replaces. Before 0.26.0 it asserted
    that ``write`` covered BOTH telemetry ingest and config mutation, which is
    what made the scope too coarse to hand an agent. The same assertion now
    reads the other way: no ingest route may be left on ``write``, or the
    split has regressed and an agent key is back to being able to rewrite
    provider configuration.
    """
    write_rows = [r for r in matrix.routes if r.auth is RouteAuth.SCOPE_WRITE]
    ingest = {r.path for r in write_rows if r.path.startswith("/v1/ingest")}
    assert not ingest, (
        f"{sorted(ingest)} are telemetry ingest but still gated by write; "
        "they belong behind require_ingest_principal (VG-SEC-003)"
    )
    config = {
        r.path
        for r in write_rows
        if r.path.startswith(("/v1/providers", "/v1/models", "/v1/projects"))
    }
    assert config, "write must still cover config mutation; got none"


def test_ingest_scope_covers_every_telemetry_write(matrix):
    """The other half: every ingest route actually sits behind the new gate."""
    ingest_rows = [r for r in matrix.routes if r.auth is RouteAuth.SCOPE_INGEST]
    assert {r.path for r in ingest_rows} == {
        "/v1/ingest",
        "/v1/ingest/turns",
        "/v1/ingest/tool-calls",
        "/v1/ingest/dead-air",
        "/v1/agents/heartbeat",
        "/v1/calls/observations",
        "/v1/accounting/prepare",
        "/v1/accounting/usage",
    }
    assert all(r.status is ContractStatus.ENFORCED for r in ingest_rows)


def test_health_routes_are_not_tenant_scoped(matrix):
    """Liveness probes must never be classified as carrying tenant data."""
    by_key = matrix.by_key()
    for key in (("GET", "/health"), ("GET", "/api/_dashboard/health")):
        assert by_key[key].status is ContractStatus.ENFORCED
        assert not by_key[key].tenant_scoped


def test_require_scope_closure_shape_is_stable():
    """The matrix generator recovers the scope from this closure.

    Assert the shape it depends on directly, so a refactor of ``_deps.py``
    fails here with an actionable message rather than silently mislabelling
    every row in the matrix.
    """
    from voicegateway.server.api._deps import require_scope

    dep = require_scope("write")
    assert dep.__qualname__ == "require_scope.<locals>._dep"
    assert dep.__code__.co_freevars == ("scope",), (
        "require_scope's closure changed shape; update classify() in "
        "tools/scripts/gen_authorization_matrix.py to match"
    )
    cell = dep.__closure__[dep.__code__.co_freevars.index("scope")]
    assert cell.cell_contents == "write"
