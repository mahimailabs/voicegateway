"""``calls.answer_latency_ms`` + ``answer_latency_source``: the headline number.

This is the one number the product claims as new ("the caller heard 4.1 s of ring
because the agent took 3.8 s to publish audio"), so these tests are mostly about
honesty rather than arithmetic:

* a number is published only when both inputs exist and the interval is one the
  clocks support -- otherwise the column stays **NULL**, never 0, because a 0
  reads as "the caller heard no ring at all";
* a weaker source never overwrites a stronger one, in **any** arrival order,
  including a redelivered webhook arriving after a load worker's measured
  ``sipp_rtd``;
* the source is a closed set, and a caller may only report the one source it can
  actually measure;
* what IS derived stays reproducible from the leg rows a reader is shown, so the
  waterfall and the headline number cannot disagree.

Webhook delivery is neither ordered nor exactly-once, so "any arrival order" is
the normal case here, not an edge case.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from voicegateway.models.call_leg_model import CallLeg
from voicegateway.models.call_model import Call  # noqa: F401 - registers table
from voicegateway.repository import calls_repository as repo

# 4100 ms of ring: the number in the product claim.
_CALLER_JOINED_MS = 1_800_000_000_100
_AGENT_PUBLISHED_MS = 1_800_000_004_200
_ANSWER_MS = _AGENT_PUBLISHED_MS - _CALLER_JOINED_MS


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    session = AsyncSession(engine)
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


async def _caller_joined(
    db: AsyncSession,
    call_id: str,
    at_ms: int = _CALLER_JOINED_MS,
    *,
    source: str | None = None,
    sid: str = "PA_caller",
    kind: str | None = "SIP",
) -> None:
    """The ``participant_joined`` half of the computation."""
    await repo.upsert_call_leg(
        db,
        call_id=call_id,
        participant_sid=sid,
        kind=kind,
        joined_at_ms=at_ms,
        source=source,
    )


async def _agent_published(
    db: AsyncSession,
    call_id: str,
    at_ms: int = _AGENT_PUBLISHED_MS,
    *,
    source: str | None = None,
    sid: str = "PA_agent",
    kind: str | None = "AGENT",
) -> None:
    """The ``track_published`` half: the audio that releases the 200 OK."""
    await repo.upsert_call_leg(
        db,
        call_id=call_id,
        participant_sid=sid,
        kind=kind,
        first_audio_track_at_ms=at_ms,
        audio_track_sid="TR_1",
        audio_codec="audio/opus",
        source=source,
    )


async def _latency(db: AsyncSession, call_id: str) -> tuple[int | None, str | None]:
    row = await repo.get_call(db, call_id)
    assert row is not None
    return row.answer_latency_ms, row.answer_latency_source


# --- the zero-instrumentation proxy -----------------------------------------


async def test_the_webhook_proxy_is_the_zero_instrumentation_default(
    db: AsyncSession,
) -> None:
    """Two webhooks and nothing else produce the number.

    ``livekit-sip`` withholds the 200 OK until it subscribes to an audio track,
    so the agent's first publish gates the caller's ring.
    """
    call_id = await repo.upsert_call(db, origin="webhook", room_sid="RM_proxy")
    await _caller_joined(db, call_id)
    await _agent_published(db, call_id)

    assert await _latency(db, call_id) == (_ANSWER_MS, "webhook_proxy")


async def test_either_arrival_order_yields_the_same_number(db: AsyncSession) -> None:
    """``track_published`` may be delivered before ``participant_joined``."""
    call_id = await repo.upsert_call(db, origin="webhook", room_sid="RM_order")
    await _agent_published(db, call_id)
    assert await _latency(db, call_id) == (None, None)  # not sourceable yet

    await _caller_joined(db, call_id)
    assert await _latency(db, call_id) == (_ANSWER_MS, "webhook_proxy")


async def test_redelivery_changes_nothing(db: AsyncSession) -> None:
    """Every event may arrive more than once; the number must not move."""
    call_id = await repo.upsert_call(db, origin="webhook", room_sid="RM_redeliver")
    for _ in range(3):
        await _caller_joined(db, call_id)
        await _agent_published(db, call_id)
        assert await _latency(db, call_id) == (_ANSWER_MS, "webhook_proxy")


async def test_epoch_millisecond_inputs_do_not_wrap(db: AsyncSession) -> None:
    """The inputs are ~1.8e12; an INT4 column would have wrapped them."""
    call_id = await repo.upsert_call(db, origin="webhook", room_sid="RM_big")
    await _caller_joined(db, call_id)
    await _agent_published(db, call_id)

    legs = await repo.list_call_legs(db, call_id)
    assert legs[0].joined_at_ms == _CALLER_JOINED_MS
    assert legs[1].first_audio_track_at_ms == _AGENT_PUBLISHED_MS
    assert await _latency(db, call_id) == (4100, "webhook_proxy")


# --- missing inputs stay NULL, never 0 --------------------------------------


async def test_a_call_with_no_legs_derives_nothing(db: AsyncSession) -> None:
    call_id = await repo.upsert_call(db, origin="webhook", room_sid="RM_bare")
    assert await _latency(db, call_id) == (None, None)


async def test_no_agent_leg_derives_nothing(db: AsyncSession) -> None:
    """The caller rang and nobody ever joined: NULL, not 0."""
    call_id = await repo.upsert_call(db, origin="webhook", room_sid="RM_noagent")
    await _caller_joined(db, call_id)
    assert await _latency(db, call_id) == (None, None)


async def test_an_agent_that_never_published_audio_derives_nothing(
    db: AsyncSession,
) -> None:
    """The row that matters most in a load test must not get a fabricated ring."""
    call_id = await repo.upsert_call(db, origin="webhook", room_sid="RM_nopublish")
    await _caller_joined(db, call_id)
    await repo.upsert_call_leg(
        db,
        call_id=call_id,
        participant_sid="PA_agent",
        kind="AGENT",
        joined_at_ms=_CALLER_JOINED_MS + 900,
        left_at_ms=_CALLER_JOINED_MS + 5_000,
        disconnect_reason="CLIENT_INITIATED",
    )
    assert await _latency(db, call_id) == (None, None)


async def test_an_unobserved_caller_join_derives_nothing(db: AsyncSession) -> None:
    call_id = await repo.upsert_call(db, origin="webhook", room_sid="RM_nojoin")
    await repo.upsert_call_leg(
        db, call_id=call_id, participant_sid="PA_caller", kind="SIP"
    )
    await _agent_published(db, call_id)
    assert await _latency(db, call_id) == (None, None)


async def test_a_call_with_no_sip_leg_has_no_ring_to_measure(db: AsyncSession) -> None:
    """A web participant is connected the instant it joins.

    Its time-to-first-audio is a different number and must not be published under
    this name.
    """
    call_id = await repo.upsert_call(db, origin="webhook", room_sid="RM_web")
    await _caller_joined(db, call_id, sid="PA_browser", kind="STANDARD")
    await _agent_published(db, call_id)
    assert await _latency(db, call_id) == (None, None)


async def test_legs_of_unknown_kind_derive_nothing(db: AsyncSession) -> None:
    """Without ``kind`` neither the caller nor the agent can be identified, and
    guessing would silently swap them."""
    call_id = await repo.upsert_call(db, origin="webhook", room_sid="RM_nokind")
    await _caller_joined(db, call_id, kind=None)
    await _agent_published(db, call_id, kind=None)
    assert await _latency(db, call_id) == (None, None)


# --- an interval the clocks do not support is not a measurement -------------


async def test_clock_skew_the_wrong_way_is_not_a_measurement(db: AsyncSession) -> None:
    """The publish cannot precede the join: livekit-sip holds the 200 OK for it.

    Both leg timestamps are still stored as observed -- only the derived interval
    is withheld, exactly as ``duration_ms`` behaves for a negative span.
    """
    call_id = await repo.upsert_call(db, origin="agent", room_sid="RM_skew")
    await _caller_joined(db, call_id, _AGENT_PUBLISHED_MS)
    await _agent_published(db, call_id, _CALLER_JOINED_MS)

    assert await _latency(db, call_id) == (None, None)
    legs = {leg.participant_sid: leg for leg in await repo.list_call_legs(db, call_id)}
    assert legs["PA_caller"].joined_at_ms == _AGENT_PUBLISHED_MS
    assert legs["PA_agent"].first_audio_track_at_ms == _CALLER_JOINED_MS


async def test_a_zero_interval_is_not_a_measurement(db: AsyncSession) -> None:
    """A webhook ``created_at`` is whole seconds, so a same-second publish
    subtracts to 0 -- which would claim the caller heard no ring at all."""
    call_id = await repo.upsert_call(db, origin="webhook", room_sid="RM_zero")
    await _caller_joined(db, call_id, _CALLER_JOINED_MS)
    await _agent_published(db, call_id, _CALLER_JOINED_MS)
    assert await _latency(db, call_id) == (None, None)


async def test_an_absurd_interval_is_rejected(db: AsyncSession) -> None:
    """Above the ceiling it is a clock disagreement, not a slow agent."""
    call_id = await repo.upsert_call(db, origin="webhook", room_sid="RM_absurd")
    await _caller_joined(db, call_id, _CALLER_JOINED_MS)
    await _agent_published(db, call_id, _CALLER_JOINED_MS + 300_001)
    assert await _latency(db, call_id) == (None, None)


async def test_the_ceiling_is_generous_enough_not_to_censor_a_bad_agent(
    db: AsyncSession,
) -> None:
    """The guard catches broken inputs, not bad performance: five minutes of ring
    is a terrible number, and it is still published as one."""
    call_id = await repo.upsert_call(db, origin="webhook", room_sid="RM_slow")
    await _caller_joined(db, call_id, _CALLER_JOINED_MS)
    await _agent_published(db, call_id, _CALLER_JOINED_MS + 300_000)
    assert await _latency(db, call_id) == (300_000, "webhook_proxy")


async def test_a_seconds_scale_unit_bug_is_rejected(db: AsyncSession) -> None:
    """Epoch seconds where epoch ms was meant: the difference is ~57 years."""
    call_id = await repo.upsert_call(db, origin="agent", room_sid="RM_units")
    await _caller_joined(db, call_id, 1_800_000_000)  # seconds, not ms
    await _agent_published(db, call_id, _AGENT_PUBLISHED_MS)
    assert await _latency(db, call_id) == (None, None)


# --- more than one leg of a kind --------------------------------------------


async def test_the_first_sip_leg_to_join_is_the_caller(db: AsyncSession) -> None:
    """A transfer or SIP REFER adds a second SIP leg; the caller rang first."""
    call_id = await repo.upsert_call(db, origin="webhook", room_sid="RM_transfer")
    await _caller_joined(db, call_id)
    await _caller_joined(db, call_id, _CALLER_JOINED_MS + 20_000, sid="PA_transferee")
    await _agent_published(db, call_id)
    assert await _latency(db, call_id) == (_ANSWER_MS, "webhook_proxy")


async def test_the_first_agent_publish_is_what_released_the_ring(
    db: AsyncSession,
) -> None:
    call_id = await repo.upsert_call(db, origin="webhook", room_sid="RM_twoagents")
    await _caller_joined(db, call_id)
    await _agent_published(db, call_id, _AGENT_PUBLISHED_MS + 6_000, sid="PA_agent_b")
    await _agent_published(db, call_id)
    assert await _latency(db, call_id) == (_ANSWER_MS, "webhook_proxy")


# --- provenance: what makes a self-report better than the proxy -------------


async def test_self_reported_timestamps_earn_agent_report(db: AsyncSession) -> None:
    """Both inputs from one process's millisecond clock: the stronger source."""
    call_id = await repo.upsert_call(db, origin="agent", room_sid="RM_selfreport")
    await _caller_joined(db, call_id, source="agent")
    await _agent_published(db, call_id, source="agent")

    assert await _latency(db, call_id) == (_ANSWER_MS, "agent_report")
    legs = {leg.participant_sid: leg for leg in await repo.list_call_legs(db, call_id)}
    assert legs["PA_caller"].joined_at_source == "agent"
    assert legs["PA_agent"].first_audio_track_at_source == "agent"


async def test_a_load_workers_self_report_also_earns_agent_report(
    db: AsyncSession,
) -> None:
    """A load worker is an in-process observer too, with a real ms clock."""
    call_id = await repo.upsert_call(db, origin="loadgen", room_sid="RM_worker")
    await _caller_joined(db, call_id, source="loadgen")
    await _agent_published(db, call_id, source="loadgen")
    assert await _latency(db, call_id) == (_ANSWER_MS, "agent_report")


async def test_the_source_names_the_weaker_of_the_two_clocks(db: AsyncSession) -> None:
    """One webhook timestamp is enough to make the whole subtraction coarse."""
    call_id = await repo.upsert_call(db, origin="webhook", room_sid="RM_mixed")
    await _caller_joined(db, call_id, source="webhook")
    await _agent_published(db, call_id, source="agent")
    assert await _latency(db, call_id) == (_ANSWER_MS, "webhook_proxy")


async def test_an_unstamped_writer_is_treated_as_webhook_precision(
    db: AsyncSession,
) -> None:
    """ "The writer did not say" must under-claim, never over-claim."""
    call_id = await repo.upsert_call(db, origin="agent", room_sid="RM_unstamped")
    await _caller_joined(db, call_id, source="agent")
    await _agent_published(db, call_id, source=None)
    assert await _latency(db, call_id) == (_ANSWER_MS, "webhook_proxy")


async def test_provenance_follows_the_value_that_won_the_merge(
    db: AsyncSession,
) -> None:
    """The stored source must stay reproducible from the stored timestamps.

    A later webhook whose truncated timestamp loses ``keep-earliest`` leaves the
    agent's value -- and its provenance -- in place. One that wins takes the
    provenance with it, and the derived source degrades to match.
    """
    call_id = await repo.upsert_call(db, origin="agent", room_sid="RM_merge")
    await _caller_joined(db, call_id, source="agent")
    await _agent_published(db, call_id, source="agent")

    # Later than the agent's observation: it loses the merge and changes nothing.
    await _agent_published(db, call_id, _AGENT_PUBLISHED_MS + 800, source="webhook")
    assert await _latency(db, call_id) == (_ANSWER_MS, "agent_report")

    # Earlier: it wins the merge, so the stored value IS webhook-precision now.
    await _agent_published(db, call_id, _AGENT_PUBLISHED_MS - 200, source="webhook")
    assert await _latency(db, call_id) == (_ANSWER_MS - 200, "webhook_proxy")
    legs = {leg.participant_sid: leg for leg in await repo.list_call_legs(db, call_id)}
    assert legs["PA_agent"].first_audio_track_at_source == "webhook"


# --- precedence: a weaker source never overwrites a stronger one ------------


async def test_a_reported_sipp_rtd_outranks_the_proxy(db: AsyncSession) -> None:
    """The true INVITE->200 wall time replaces the proxy for the same call."""
    call_id = await repo.upsert_call(db, origin="loadgen", attempt_id="att-1")
    await _caller_joined(db, call_id)
    await _agent_published(db, call_id)
    assert await _latency(db, call_id) == (_ANSWER_MS, "webhook_proxy")

    await repo.upsert_call(
        db,
        origin="loadgen",
        attempt_id="att-1",
        reported_answer_latency_ms=4_380,
        reported_answer_latency_source="sipp_rtd",
    )
    assert await _latency(db, call_id) == (4_380, "sipp_rtd")


@pytest.mark.parametrize("replay_order", [("join", "publish"), ("publish", "join")])
async def test_a_webhook_cannot_clobber_a_reported_sipp_rtd(
    db: AsyncSession, replay_order: tuple[str, str]
) -> None:
    """The named hazard, in both arrival orders and with redelivery.

    LiveKit delivers out of order and retries, so the proxy WILL be recomputed
    after the load worker's measured number is already stored.
    """
    call_id = await repo.upsert_call(
        db,
        origin="loadgen",
        attempt_id="att-2",
        room_sid="RM_precedence",
        reported_answer_latency_ms=4_380,
        reported_answer_latency_source="sipp_rtd",
    )
    assert await _latency(db, call_id) == (4_380, "sipp_rtd")

    events = {"join": _caller_joined, "publish": _agent_published}
    for _ in range(2):  # deliver, then redeliver
        for name in replay_order:
            await events[name](db, call_id)
            assert await _latency(db, call_id) == (4_380, "sipp_rtd")

    # And a webhook that would have derived NOTHING cannot erase it either.
    await _agent_published(db, call_id, _CALLER_JOINED_MS - 1_000)
    assert await _latency(db, call_id) == (4_380, "sipp_rtd")


async def test_a_redelivered_report_is_idempotent(db: AsyncSession) -> None:
    for _ in range(3):
        await repo.upsert_call(
            db,
            origin="loadgen",
            attempt_id="att-3",
            reported_answer_latency_ms=4_380,
            reported_answer_latency_source="sipp_rtd",
        )
    row = await repo.get_call_by_room_sid(db, "RM_none")
    assert row is None
    calls = await repo.list_calls(db, is_probe=None)
    assert len(calls) == 1
    assert (calls[0].answer_latency_ms, calls[0].answer_latency_source) == (
        4_380,
        "sipp_rtd",
    )


async def test_the_derived_value_is_cleared_when_the_legs_stop_supporting_it(
    db: AsyncSession,
) -> None:
    """A stale number next to a contradicting leg timeline is worse than NULL."""
    call_id = await repo.upsert_call(db, origin="webhook", room_sid="RM_clear")
    await _caller_joined(db, call_id)
    await _agent_published(db, call_id)
    assert await _latency(db, call_id) == (_ANSWER_MS, "webhook_proxy")

    # A late event carrying an EARLIER publish: the interval is now non-positive,
    # so there is no longer a measurement to show.
    await _agent_published(db, call_id, _CALLER_JOINED_MS - 5)
    assert await _latency(db, call_id) == (None, None)


# --- the closed set ---------------------------------------------------------


def test_the_source_set_is_closed_and_ranked_strongest_first() -> None:
    assert repo.ANSWER_LATENCY_SOURCES == (
        "sipp_rtd",
        "agent_report",
        "webhook_proxy",
    )
    ranks = [repo._ANSWER_LATENCY_RANK[s] for s in repo.ANSWER_LATENCY_SOURCES]
    assert ranks == sorted(ranks, reverse=True)
    # Only the source a caller can actually measure end to end is reportable.
    assert repo.REPORTED_ANSWER_LATENCY_SOURCES == {"sipp_rtd"}


@pytest.mark.parametrize("source", ["agent_report", "webhook_proxy", "guessed", ""])
async def test_a_derived_or_unknown_source_cannot_be_reported(
    db: AsyncSession, source: str
) -> None:
    """Only ``sipp_rtd`` is a caller's measurement; the rest are derived here.

    Accepting a caller-asserted ``webhook_proxy`` would put a second answer-latency
    computation into the product, which is the thing this column exists to avoid.
    """
    with pytest.raises(ValueError, match="cannot be reported"):
        await repo.upsert_call(
            db,
            origin="loadgen",
            attempt_id="att-bad",
            reported_answer_latency_ms=4_100,
            reported_answer_latency_source=source,
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"reported_answer_latency_ms": 4_100},
        {"reported_answer_latency_source": "sipp_rtd"},
    ],
)
async def test_a_reported_value_and_its_source_travel_together(
    db: AsyncSession, kwargs: dict[str, object]
) -> None:
    with pytest.raises(ValueError, match="both the value and its source"):
        await repo.upsert_call(
            db,
            origin="loadgen",
            attempt_id="att-half",
            **kwargs,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("reported", [0, -1, 300_001])
async def test_an_implausible_report_is_refused_and_erases_nothing(
    db: AsyncSession, reported: int
) -> None:
    """A worker reporting nonsense must not wipe a number the webhooks derived."""
    call_id = await repo.upsert_call(db, origin="loadgen", attempt_id="att-4")
    await _caller_joined(db, call_id)
    await _agent_published(db, call_id)

    await repo.upsert_call(
        db,
        origin="loadgen",
        attempt_id="att-4",
        reported_answer_latency_ms=reported,
        reported_answer_latency_source="sipp_rtd",
    )
    assert await _latency(db, call_id) == (_ANSWER_MS, "webhook_proxy")


# --- schema shape -----------------------------------------------------------


def test_the_provenance_columns_are_nullable() -> None:
    """A leg written before this column existed has an unknown writer, and NULL
    is how the schema says so. There is no backfill and no default."""
    for name in ("joined_at_source", "first_audio_track_at_source"):
        column = CallLeg.__table__.c[name]
        assert column.nullable
        assert column.server_default is None
