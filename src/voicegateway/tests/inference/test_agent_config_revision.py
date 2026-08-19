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


# --------------------------------------------------------------------------
# Two revisions in one window must be separable, not blended
# --------------------------------------------------------------------------


async def _seeded(tmp_path):
    """A window holding two revisions plus one un-stamped row."""
    from voicegateway.services.storage_service import StorageService

    storage = StorageService(str(tmp_path / "rev.db"))
    await storage._ensure_initialized()
    tracker = CostTracker()
    for rev, cost in (("v1", 0.10), ("v1", 0.20), ("v2", 0.05), (None, 0.01)):
        record = tracker.create_record(
            model_id="openai/gpt-4o-mini",
            modality="llm",
            provider="openai",
            project="default",
            revision=rev,
        )
        record.cost_usd = cost
        await storage.log_request(record)
    return storage


async def test_two_revisions_produce_separable_aggregates(tmp_path) -> None:
    """The reason the column exists.

    Two revisions live in one window is what every canary and every gradual
    rollout looks like. Blended, the p95 is a p95 of two different agents with
    nothing saying so.
    """
    storage = await _seeded(tmp_path)
    by_rev = await storage.get_cost_by_revision("all")
    await storage.aclose()
    assert round(by_rev["v1"]["cost"], 4) == 0.30
    assert by_rev["v1"]["requests"] == 2
    assert round(by_rev["v2"]["cost"], 4) == 0.05


async def test_unstamped_rows_group_rather_than_vanish(tmp_path) -> None:
    """They are the population being migrated away from during a rollout.

    Dropping them would also make this rollup disagree with the unfiltered
    total for no visible reason.
    """
    storage = await _seeded(tmp_path)
    by_rev = await storage.get_cost_by_revision("all")
    summary = await storage.get_cost_summary("all")
    await storage.aclose()
    assert round(by_rev[""]["cost"], 4) == 0.01
    assert round(sum(v["cost"] for v in by_rev.values()), 4) == round(
        summary["total"], 4
    )


async def test_filtering_by_revision_selects_only_that_one(tmp_path) -> None:
    storage = await _seeded(tmp_path)
    only_v1 = await storage.get_cost_summary("all", revision="v1")
    await storage.aclose()
    assert round(only_v1["total"], 4) == 0.30


async def test_the_empty_string_filter_selects_the_unstamped(tmp_path) -> None:
    """Matching the tenant and agent filters, and the only way to ask
    "what did the un-stamped agents do"."""
    storage = await _seeded(tmp_path)
    unstamped = await storage.get_cost_summary("all", revision="")
    await storage.aclose()
    assert round(unstamped["total"], 4) == 0.01


# --------------------------------------------------------------------------
# An error row names the provider that failed
# --------------------------------------------------------------------------


def test_an_error_row_carries_the_provider_and_model_that_failed() -> None:
    """Reported from a live collector: 20 error rows with an empty provider.

    `_on_error` recorded model_id="" and provider="" while the success path
    called `component_identity` on the same kind of object. So the one row type
    that exists to answer "which provider is throwing 429s" was the one row type
    that could not answer it.

    The identity was never missing. It sits on `event.source`, and the error
    text already carried it as `label='livekit.plugins.cerebras.llm.LLM'`.
    """
    from voicegateway.inference.session import capture as capture_mod

    class _CerebrasLLM:
        pass

    # Same module path shape the resolver reads: livekit.plugins.<provider>.
    _CerebrasLLM.__module__ = "livekit.plugins.cerebras.llm"
    _CerebrasLLM.model = "gemma-4-31b"

    provider, model_id = capture_mod.component_identity(_CerebrasLLM())
    assert provider == "cerebras"
    assert model_id == "cerebras/gemma-4-31b"


def test_an_error_with_no_source_stays_blank_rather_than_inventing_a_name() -> None:
    """Absent is not "unknown".

    An error with no component attached is genuinely unattributed. Stamping it
    "unknown" would put it in a group alongside components that WERE present and
    could not be read, which are a different fact and a different fix.
    """
    import inspect

    from voicegateway.inference.session import capture as capture_mod

    src = inspect.getsource(capture_mod.MetricCapture._on_error)
    assert 'if source is not None else ("", "")' in src
