"""Wave 0: the authorization matrix must stay bound to the real app.

The matrix is only worth having if it cannot drift. Three tests do that work:

- **bijection** — every live ``APIRoute`` has exactly one row and every row
  names a live route, so adding an endpoint without classifying it fails CI;
- **classification** — the ``auth`` recorded on each row equals what the
  resolved FastAPI dependency graph actually says today, so a row cannot claim
  a route is guarded when it is not;
- **absence** — the planned routes genuinely are not servable yet.

Together they mean the matrix cannot disagree with routes, auth wiring, or the
threat model without something going red.
"""

from __future__ import annotations

import importlib.util

import pytest

from voicegateway.schemas.telemetry.security_schema import (
    ContractStatus,
    RouteAuth,
    ScopeName,
    load_authorization_matrix,
    load_threat_model,
)
from voicegateway.tests.server._telemetry_harness import (
    canonical_route_auth,
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
    assert matrix.planned_routes


def test_every_gap_row_names_a_minted_gap(matrix):
    """No row may invent a gap id the threat model never minted."""
    minted = load_threat_model().gap_ids()
    orphans = sorted(matrix.gap_ids() - minted)
    assert not orphans, f"matrix references unminted gap ids: {orphans}"


def test_gap_rows_carry_a_wave_matching_the_threat_model(matrix):
    """A row and its threat entry must not disagree about when it is fixed."""
    entries = load_threat_model().by_gap_id()
    mismatched = [
        f"{r.method} {r.path}: row wave {r.wave} != {r.gap_id} wave "
        f"{entries[r.gap_id].wave}"
        for r in matrix.routes
        if r.gap_id is not None and r.wave != entries[r.gap_id].wave
    ]
    assert not mismatched, mismatched


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


def test_bijection_is_exact(matrix, live):
    """State the cardinality directly, so an off-by-one cannot hide."""
    assert len(matrix.routes) == len(live)
    assert matrix.keys() == set(live)


def test_canonical_inventory_ignores_parent_router_mutation(live):
    """The child-process inventory must not inherit altered parent routers."""
    from voicegateway.server.routes import api_router, dashboard_router, system_router

    routers = (system_router, api_router, dashboard_router)
    saved = [list(router.routes) for router in routers]
    try:
        for router in routers:
            router.routes.clear()
        assert canonical_route_auth() == live
    finally:
        for router, routes in zip(routers, saved, strict=True):
            router.routes[:] = routes


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


def test_write_scope_spans_ingest_and_config(matrix):
    """Evidence for VG-SEC-003: splitting write is semantic, not a rename.

    The count is pinned rather than merely non-empty because 18 is quoted as a
    fact in the VG-SEC-003 threat entry and on the docs page. A looser
    assertion lets the code drift away from a number the prose still claims.
    """
    write_rows = [r for r in matrix.routes if r.auth is RouteAuth.SCOPE_WRITE]
    assert len(write_rows) == 18, (
        f"{len(write_rows)} routes are gated by the write scope, but "
        "VG-SEC-003 and docs/architecture/observability-security.md both say "
        "18. Update the count in all three places together."
    )
    ingest = {r.path for r in write_rows if r.path.startswith("/v1/ingest")}
    config = {
        r.path
        for r in write_rows
        if r.path.startswith(("/v1/providers", "/v1/models", "/v1/projects"))
    }
    assert ingest and config, (
        "the write scope must be shown to cover both telemetry ingest and "
        f"config mutation; got ingest={ingest} config={config}"
    )


def test_health_routes_are_not_tenant_scoped(matrix):
    """Liveness probes must never be classified as carrying tenant data."""
    by_key = matrix.by_key()
    for key in (("GET", "/health"), ("GET", "/api/_dashboard/health")):
        assert by_key[key].status is ContractStatus.ENFORCED
        assert not by_key[key].tenant_scoped


# --------------------------------------------------------------------------
# Absence guards for the planned routes
# --------------------------------------------------------------------------


def test_planned_routes_do_not_exist_yet(matrix, live):
    """A planned route that already ships is a stale contract, not a plan."""
    already = sorted(matrix.planned_keys() & set(live))
    assert not already, (
        f"{already} now exist; move them into routes and close their gap"
    )


def test_planned_routes_name_a_scope_that_must_exist_first(matrix):
    """Every planned route depends on a scope the threat model tracks."""
    minted = load_threat_model().gap_ids()
    for planned in matrix.planned_routes:
        assert planned.gap_id in minted
        assert planned.required_scope.value


def test_planned_ingest_routes_derive_their_tenant_server_side(matrix):
    """VG-SEC-015: the trace receiver must not trust a payload tenant.

    The rule is stated in ``SpanAttributes``'s docstring and again on the
    observability-contracts page, but prose is exactly what VG-SEC-001 already
    defeated once: docs/architecture/security.md promised the payload could not
    override the key-derived tenant while the tool-call writer did precisely
    that. Asserting it against the data means the receiver cannot be written
    without a row that says where its tenant comes from.
    """
    ingest = [
        planned
        for planned in matrix.planned_routes
        if planned.required_scope is ScopeName.INGEST
    ]
    assert ingest, "no planned ingest route: VG-SEC-015 has nothing to bind to"
    for planned in ingest:
        assert planned.tenant_source == "server_derived", (
            f"{planned.method} {planned.path} may not accept a payload tenant"
        )


def test_the_trace_contract_can_carry_a_payload_tenant(matrix):
    """Why VG-SEC-015 exists: the risk is real, not hypothetical.

    ``SpanRecord`` has a single tenant slot and no way to distinguish what a
    payload asserted from what the server decided, so a receiver that simply
    validates and stores an incoming span inherits the caller's tenant. Skips
    until the trace contract exists, then stands as the standing argument for
    the rule above.
    """
    if importlib.util.find_spec("voicegateway.telemetry") is None:
        pytest.skip("voicegateway.telemetry does not exist yet")

    from voicegateway.telemetry.trace_schema import SpanAttributes

    attacker_controlled = SpanAttributes(tenant_id="victim-tenant")
    assert attacker_controlled.tenant_id == "victim-tenant", (
        "SpanAttributes now rejects a caller-supplied tenant. If the contract "
        "gained a server-derived-only slot, VG-SEC-015 may be closable."
    )
    assert "VG-SEC-015" in load_threat_model().gap_ids()


@pytest.mark.parametrize(
    "path", ["/v1/telemetry/traces", "/v1/telemetry/metrics", "/v1/telemetry/logs"]
)
def test_otlp_paths_avoid_the_existing_read_endpoints(matrix, live, path):
    """The reason these are namespaced: /v1/metrics and /v1/logs are taken.

    The conventional OTLP receiver mounts are ``/v1/traces``, ``/v1/metrics``
    and ``/v1/logs``. Two of those already exist here as GET reads, so a bare
    OTLP mount would sit on the same path as an unrelated endpoint and differ
    only by method. The planned rows namespace under ``/v1/telemetry/``.
    """
    assert ("POST", path) in matrix.planned_keys()
    bare = path.replace("/v1/telemetry/", "/v1/")
    if bare in {"/v1/metrics", "/v1/logs"}:
        assert ("GET", bare) in live, (
            f"{bare} no longer exists; the namespacing rationale in the "
            "planned rows should be revisited"
        )
