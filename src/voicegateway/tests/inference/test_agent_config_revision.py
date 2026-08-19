"""Which build of the agent's configuration produced a row.

Rows carried `project`, `agent_id` and `tenant_id`, none of which says which
version of the agent was running: the prompt, the model ids, the voice, the
interruption thresholds. So "this got slower last Tuesday" was answerable only
by joining deploy logs kept somewhere else against timestamps by hand.

That join stops working the moment two versions run at once, which is what every
canary and every gradual rollout is, and that is the case where the question
matters most. It also makes the percentile views honest: a p95 computed across a
configuration change is a p95 of two different agents, and nothing said so.

The value is OPAQUE. A content hash, a git sha, a semver string and a deploy id
are all valid; nothing parses it, it is only grouped and filtered by.
"""

from __future__ import annotations

import os

import pytest

from voicegateway.inference.session.attach import _resolve_revision
from voicegateway.middleware.cost_tracker_middleware import CostTracker
from voicegateway.middleware.dead_air_detector_middleware import DeadAirEvent
from voicegateway.middleware.turn_tracker_middleware import TurnRow, TurnTracker

_ENV = "VOICEGW_AGENT_REVISION"


@pytest.fixture(autouse=True)
def _clean_env():
    before = os.environ.pop(_ENV, None)
    yield
    os.environ.pop(_ENV, None)
    if before is not None:
        os.environ[_ENV] = before


# --------------------------------------------------------------------------
# Precedence
# --------------------------------------------------------------------------


def test_the_argument_wins_over_the_environment() -> None:
    """Opposite precedence to the capture kill-switches, deliberately.

    Those are a fleet-wide operator override of what an agent asked for. This is
    the agent REPORTING a fact about itself, and a process knows its own
    revision better than the environment it was launched into.
    """
    os.environ[_ENV] = "from-env"
    assert _resolve_revision("from-arg") == "from-arg"


def test_the_environment_is_the_fallback() -> None:
    """For deployments that stamp the revision at rollout rather than in code."""
    os.environ[_ENV] = "from-env"
    assert _resolve_revision(None) == "from-env"


def test_neither_set_stays_none_rather_than_inventing_one() -> None:
    """No hostname, no timestamp, no default.

    An invented revision would silently SPLIT aggregates that belong together,
    which is worse than not grouping at all: the reader sees two agents where
    there was one and cannot tell that the split is an artefact.
    """
    assert _resolve_revision(None) is None


def test_an_empty_string_is_not_a_revision() -> None:
    """Empty is absent. Stamping "" would create a group nobody declared."""
    os.environ[_ENV] = ""
    assert _resolve_revision("") is None


# --------------------------------------------------------------------------
# It reaches every row type
# --------------------------------------------------------------------------


def test_a_cost_row_carries_it() -> None:
    record = CostTracker().create_record(
        model_id="openai/gpt-4o-mini",
        modality="llm",
        provider="openai",
        project="default",
        revision="abc123",
    )
    assert record.revision == "abc123"


def test_a_cost_row_without_one_is_unchanged() -> None:
    """Absent must behave exactly as before, so nothing existing moves."""
    record = CostTracker().create_record(
        model_id="openai/gpt-4o-mini",
        modality="llm",
        provider="openai",
        project="default",
    )
    assert record.revision is None


async def test_a_turn_row_carries_it() -> None:
    tracker = TurnTracker(flush_size=1000, revision="abc123")
    await tracker.on_user_started_speaking(session_id="s", at_ms=1000)
    await tracker.on_user_stopped_speaking(session_id="s", at_ms=2000, precise=True)
    await tracker.on_agent_audio_first_frame(session_id="s", at_ms=2300)
    assert tracker._sessions["s"].buffered_turns[0].revision == "abc123"


async def test_a_turn_left_open_at_session_close_carries_it_too() -> None:
    """The tail row written on close is a row like any other and must match.

    Missing it would make the LAST turn of every session fall out of a
    per-revision aggregate, which is the turn most likely to be the interesting
    one when a session ended badly.
    """
    written: list[TurnRow] = []

    async def _flush(rows: list[TurnRow]) -> None:
        written.extend(rows)

    tracker = TurnTracker(flush_callback=_flush, flush_size=1000, revision="abc123")
    await tracker.on_user_started_speaking(session_id="s", at_ms=1000)
    await tracker.on_user_stopped_speaking(session_id="s", at_ms=2000, precise=True)
    await tracker.close_session("s")
    assert written, "the open turn was not written on close"
    assert written[-1].revision == "abc123"


def test_a_dead_air_event_carries_it() -> None:
    event = DeadAirEvent(
        session_id="s",
        started_at_ms=1000,
        duration_ms=5000,
        threshold_used_ms=4000,
        revision="abc123",
    )
    assert event.revision == "abc123"


def test_the_turn_row_default_is_absent() -> None:
    row = TurnRow(
        session_id="s", turn_index=0, caller_speak_start_ms=0, caller_speak_end_ms=1
    )
    assert row.revision is None
