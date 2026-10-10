"""The fixtures themselves must be well-formed before anything replays them."""

from __future__ import annotations

import pytest

from voicegateway.tests.fixtures.security._loader import (
    fixture_paths,
    load_all,
    load_fixture,
)


def test_every_case_file_validates():
    """A malformed fixture must fail here, not halfway through a runner."""
    assert len(load_all()) == len(fixture_paths()) >= 5


@pytest.mark.parametrize("path", fixture_paths(), ids=lambda p: p.stem)
def test_case_id_matches_its_filename(path):
    """The filename is how a case is cited in docs, so it must be the id."""
    assert load_fixture(path).case_id == path.stem


def test_both_guarantees_and_defects_are_represented():
    """The format must express a satisfied rule, not only a broken one."""
    kinds = {f.kind for f in load_all()}
    assert {"guarantee", "characterization", "absence"} <= kinds


def test_characterizations_actually_differ_from_their_contract():
    """Belt and braces over the model validator, stated as its own test."""
    for fixture in load_all():
        if fixture.kind == "characterization":
            assert fixture.observed != fixture.contract, fixture.case_id
