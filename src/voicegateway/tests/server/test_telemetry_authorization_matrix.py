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

import pytest

from voicegateway.schemas.telemetry.security_schema import (
    ContractStatus,
    RouteAuth,
    load_authorization_matrix,
    load_threat_model,
)
from voicegateway.tests.server._telemetry_harness import _Harness, live_route_auth


@pytest.fixture(scope="module")
def app():
    """One app for the whole module: these tests never mutate it."""
    harness = _Harness()
    try:
        yield harness.app
    finally:
        harness.cleanup()


@pytest.fixture(scope="module")
def matrix():
    return load_authorization_matrix()


@pytest.fixture(scope="module")
def live(app):
    return live_route_auth(app)


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
    """Evidence for VG-SEC-003: splitting write is semantic, not a rename."""
    write_rows = [r for r in matrix.routes if r.auth is RouteAuth.SCOPE_WRITE]
    assert write_rows, "no route is gated by the write scope"
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
