"""Wave 0 keystone: the gap ids must agree everywhere they appear.

This is the test that turns eight prose deliverables into one artifact. A gap
id is minted once in ``threat_model.json`` and then referenced from the
authorization matrix, the fixtures and the docs page. If those references can
drift, the contract is documentation. If they cannot, it is a checklist whose
completion is mechanically verifiable.

On what is asserted equal, and what is only asserted consistent: the plan
called for four equal id sets. That is too strong, and asserting it would have
forced padding. Four of the fourteen gaps are route-shaped and appear in the
matrix. Three have a request-shaped expression and appear as fixtures. The
rest are properties of auth internals, crypto and the audit writer, and have
no route or request to attach to. Inventing rows for them would make the
matrix lie about what it covers.

So the strict equality holds where it is meaningful, between the threat model
and the docs page, which are the two places that must describe *every* gap.
Everywhere else the assertion is referential integrity: nothing may cite a gap
that was never minted, nothing may disagree about a wave, and no minted gap
may be orphaned from all three of matrix, fixture and docs at once.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from voicegateway.schemas.telemetry.security_schema import (
    GAP_ID_PATTERN,
    ThreatId,
    load_authorization_matrix,
    load_threat_model,
)
from voicegateway.tests.fixtures.security._loader import load_all

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DOCS_PAGE = _REPO_ROOT / "docs" / "architecture" / "observability-security.md"
_GAP_IN_TEXT = re.compile(r"VG-SEC-\d{3}")


def _docs_text() -> str:
    """Read the docs page, or skip when running outside a full checkout.

    The sdist ships ``src/`` but not ``docs/``, and the packaging config
    deliberately keeps the suite runnable from an unpacked sdist. Skipping
    there is correct; failing would make a green checkout and a green sdist
    disagree for a reason that has nothing to do with the contract.
    """
    if not _DOCS_PAGE.exists():
        pytest.skip(f"{_DOCS_PAGE} not present (running outside a checkout)")
    return _DOCS_PAGE.read_text(encoding="utf-8")


def _minted() -> set[str]:
    return load_threat_model().gap_ids()


# --------------------------------------------------------------------------
# The minted set itself
# --------------------------------------------------------------------------


def test_gap_ids_are_contiguous_and_well_formed():
    """A hole in the numbering usually means a gap was dropped, not renamed.

    The count is pinned deliberately. Adding a gap should be a decision, so
    minting one and bumping this number is meant to be a two-line change made
    on purpose rather than something that happens by accident.
    """
    minted = sorted(_minted())
    assert len(minted) == 15
    for index, gap_id in enumerate(minted, start=1):
        assert re.fullmatch(GAP_ID_PATTERN, gap_id), gap_id
        assert gap_id == f"VG-SEC-{index:03d}", (
            f"expected VG-SEC-{index:03d} at position {index}, found {gap_id}"
        )


def test_every_threat_has_at_least_one_gap():
    """A threat with no gap is a heading, not a finding."""
    model = load_threat_model()
    empty = [t.value for t in ThreatId if not model.by_threat(t)]
    assert not empty, f"threats with no gaps: {empty}"


def test_every_gap_cites_resolvable_evidence():
    """Each claim must be checkable by opening one file at one line."""
    unresolved = []
    for entry in load_threat_model().root:
        path_part, _, line_part = entry.evidence.rpartition(":")
        source = _REPO_ROOT / path_part
        if not source.exists():
            unresolved.append(f"{entry.gap_id}: no such file {path_part}")
            continue
        line_count = len(source.read_text(encoding="utf-8").splitlines())
        if int(line_part) > line_count:
            unresolved.append(
                f"{entry.gap_id}: {entry.evidence} is past EOF ({line_count} lines)"
            )
    assert not unresolved, unresolved


# --------------------------------------------------------------------------
# Strict equality: threat model and docs must describe the same set
# --------------------------------------------------------------------------


def test_docs_page_documents_exactly_the_minted_gaps():
    """The docs page is the human index, so it must be complete and honest."""
    documented = set(_GAP_IN_TEXT.findall(_docs_text()))
    minted = _minted()
    assert documented == minted, (
        f"undocumented: {sorted(minted - documented)}; "
        f"documented but never minted: {sorted(documented - minted)}"
    )


def test_docs_page_names_every_threat():
    """Each threat needs a section, or its gaps have no context."""
    text = _docs_text()
    missing = [t.value for t in ThreatId if t.value not in text]
    assert not missing, missing


# --------------------------------------------------------------------------
# Referential integrity everywhere else
# --------------------------------------------------------------------------


def test_matrix_cites_only_minted_gaps():
    """The matrix may cover a subset, but never an invented id."""
    orphans = sorted(load_authorization_matrix().gap_ids() - _minted())
    assert not orphans, orphans


def test_fixtures_cite_only_minted_gaps():
    """Same rule for the fixtures."""
    cited = {f.gap_id for f in load_all() if f.gap_id is not None}
    orphans = sorted(cited - _minted())
    assert not orphans, orphans


def test_no_minted_gap_is_completely_orphaned():
    """Every gap must be reachable from at least one machine-checked place."""
    matrix = load_authorization_matrix().gap_ids()
    fixtures = {f.gap_id for f in load_all() if f.gap_id is not None}
    documented = set(_GAP_IN_TEXT.findall(_docs_text()))
    reachable = matrix | fixtures | documented
    orphaned = sorted(_minted() - reachable)
    assert not orphaned, f"minted but referenced nowhere: {orphaned}"


def test_waves_agree_between_threat_model_and_matrix():
    """One gap, one wave. Two answers means one of them is stale."""
    entries = load_threat_model().by_gap_id()
    disagreements = []
    matrix = load_authorization_matrix()
    for rule in matrix.routes:
        if rule.gap_id is not None and rule.wave != entries[rule.gap_id].wave:
            disagreements.append(
                f"{rule.method} {rule.path}: row says wave {rule.wave}, "
                f"{rule.gap_id} says wave {entries[rule.gap_id].wave}"
            )
    for planned in matrix.planned_routes:
        if planned.wave != entries[planned.gap_id].wave:
            disagreements.append(
                f"planned {planned.method} {planned.path}: row says wave "
                f"{planned.wave}, {planned.gap_id} says wave "
                f"{entries[planned.gap_id].wave}"
            )
    assert not disagreements, disagreements


def test_fixture_paths_named_by_the_threat_model_exist():
    """A threat entry that names a fixture must name one that is there."""
    from voicegateway.tests.fixtures.security._loader import FIXTURE_DIR

    missing = [
        f"{entry.gap_id} -> {entry.fixture}"
        for entry in load_threat_model().root
        if entry.fixture is not None and not (FIXTURE_DIR / entry.fixture).exists()
    ]
    assert not missing, missing


def test_every_gap_with_a_fixture_agrees_with_that_fixture():
    """The threat entry and the fixture must cite each other, not just exist."""
    by_case = {f.case_id: f for f in load_all()}
    mismatched = []
    for entry in load_threat_model().root:
        if entry.fixture is None:
            continue
        case_id = entry.fixture.removesuffix(".json")
        fixture = by_case.get(case_id)
        if fixture is None:
            mismatched.append(f"{entry.gap_id}: no fixture case {case_id}")
        elif fixture.gap_id != entry.gap_id:
            mismatched.append(
                f"{entry.gap_id} names {case_id}, which cites {fixture.gap_id}"
            )
    assert not mismatched, mismatched
