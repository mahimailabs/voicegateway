"""One named policy instead of four booleans nobody can name together.

Capture was four booleans on `attach()`, each with its own default and its own
environment kill-switch. The SET is the interesting thing and there was no way
to say it. An operator who wanted "timing only, nothing the caller said" had to
know that means three of the four off, get each right, repeat it at every call
site, and know which variable overrides which. That is a policy expressed as
four independent booleans, which is how a wrong combination ships without
anybody deciding to ship it.

The mapping from policy to flags is asserted here rather than described in
prose, which is the whole point of the change: prose about which booleans a
policy sets is the thing that goes stale and was already spread across three
places.
"""

from __future__ import annotations

import importlib
import os

import pytest

from voicegateway.inference.session.policy import (
    CAPTURE_POLICIES,
    CAPTURE_SWITCHES,
    resolve_policy,
)

# import_module, not `from ... import attach`: the package __init__ re-exports
# the attach FUNCTION under that name and shadows the module.
attach_mod = importlib.import_module("voicegateway.inference.session.attach")

_resolve = attach_mod._resolve_capture


# --------------------------------------------------------------------------
# The mapping, asserted rather than described
# --------------------------------------------------------------------------


def test_every_policy_sets_every_switch() -> None:
    """A policy that leaves one unset is a combination nobody decided."""
    for name, mapping in CAPTURE_POLICIES.items():
        assert set(mapping) == set(CAPTURE_SWITCHES), name


def test_standard_is_exactly_todays_defaults() -> None:
    """The compatibility claim, stated as an equality.

    If this drifts, every existing `attach()` call changes behaviour silently,
    which the acceptance explicitly forbids.
    """
    assert CAPTURE_POLICIES["standard"] == {
        "transcript": True,
        "snapshots": False,
        "turns": True,
        "dead_air": True,
    }


def test_timing_only_captures_nothing_the_caller_said() -> None:
    """The intent the issue names outright."""
    p = CAPTURE_POLICIES["timing_only"]
    assert p["transcript"] is False and p["snapshots"] is False
    assert p["turns"] is True


def test_lean_differs_from_timing_only_on_cost_not_disclosure() -> None:
    """The two axes the four booleans flattened.

    Dead air polls once a second for the life of every session; turn capture
    costs nothing between events. `lean` is the same disclosure as
    `timing_only` and a different running cost, and that is a distinction the
    booleans could express but could not name.
    """
    lean, timing = CAPTURE_POLICIES["lean"], CAPTURE_POLICIES["timing_only"]
    differing = {k for k in CAPTURE_SWITCHES if lean[k] != timing[k]}
    assert differing == {"dead_air"}


def test_debug_is_the_only_policy_that_discloses_snapshots() -> None:
    """A snapshot is the operator's own prompt and every tool payload.

    Exactly one policy should carry that, and it should be the one whose name
    says why you turned it on.
    """
    with_snapshots = {n for n, p in CAPTURE_POLICIES.items() if p["snapshots"]}
    assert with_snapshots == {"debug"}


def test_off_captures_nothing() -> None:
    assert not any(CAPTURE_POLICIES["off"].values())


# --------------------------------------------------------------------------
# Precedence
# --------------------------------------------------------------------------


def test_passing_nothing_behaves_exactly_as_before() -> None:
    """The acceptance criterion: existing calls are identical, no warning."""
    assert _resolve(None, None, None, None, None) == CAPTURE_POLICIES["standard"]


def test_the_four_booleans_still_work_on_their_own() -> None:
    """No deprecation in this change, so they must keep behaving."""
    resolved = _resolve(None, False, True, False, False)
    assert resolved == {
        "transcript": False,
        "snapshots": True,
        "turns": False,
        "dead_air": False,
    }


def test_an_explicit_argument_overrides_the_policy() -> None:
    """Which is why the four parameters are tri-state.

    With plain bool defaults there is no way to tell `transcript=True` passed
    deliberately from the default that happens to be True, so a policy could
    not be overridden in one place without being ignored everywhere.
    """
    resolved = _resolve("timing_only", True, None, None, None)
    assert resolved["transcript"] is True
    # And the rest of the policy still applies.
    assert resolved["snapshots"] is False
    assert resolved["dead_air"] is True


def test_an_unknown_policy_is_refused_not_defaulted() -> None:
    """A typo that silently selected `standard` would turn "nothing the caller
    said" into transcript capture: the exact wrong combination this prevents."""
    with pytest.raises(ValueError) as excinfo:
        resolve_policy("timing-only")  # underscores, not a hyphen
    assert "timing_only" in str(excinfo.value)


def test_resolving_returns_a_copy_not_the_shared_mapping() -> None:
    """An override must not mutate the policy table for the whole process."""
    first = _resolve("timing_only", True, None, None, None)
    second = _resolve("timing_only", None, None, None, None)
    assert first["transcript"] is True
    assert second["transcript"] is False


# --------------------------------------------------------------------------
# The kill-switches keep beating everything
# --------------------------------------------------------------------------


def test_the_environment_still_beats_a_policy() -> None:
    """A fleet-wide override a policy could cancel would not be a kill-switch.

    The env vars are applied downstream of the resolver, so they survive both
    the policy and an explicit argument.
    """
    before = os.environ.get("VOICEGW_TRANSCRIPTS")
    os.environ["VOICEGW_TRANSCRIPTS"] = "0"
    try:
        resolved = _resolve("debug", None, None, None, None)
        assert resolved["transcript"] is True  # the policy did ask for it
        # ...and the kill-switch still wins where it is applied.
        assert attach_mod._transcripts_enabled(resolved["transcript"]) is False
    finally:
        os.environ.pop("VOICEGW_TRANSCRIPTS", None)
        if before is not None:
            os.environ["VOICEGW_TRANSCRIPTS"] = before
