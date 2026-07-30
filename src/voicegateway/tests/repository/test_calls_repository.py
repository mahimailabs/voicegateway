"""The calls + call_legs repo: create-from-any-event, merge, and derived columns.

These tests pin the properties the ingest tier depends on, because webhook
delivery is neither ordered nor exactly-once: any event may create the row, a
later event never clobbers what an earlier one established, and a redelivery
never duplicates a leg or inflates a count.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from voicegateway.models.call_leg_model import CallLeg  # noqa: F401 - registers table
from voicegateway.models.call_model import Call  # noqa: F401 - registers table
from voicegateway.repository import calls_repository as repo


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


async def _count(db: AsyncSession, table: str) -> int:
    result = await db.execute(text(f"SELECT COUNT(*) FROM {table}"))
    return int(result.scalar() or 0)


# --- create-if-missing from any event ---------------------------------------


async def test_a_terminal_event_creates_the_call(db: AsyncSession) -> None:
    """participant_left may be the FIRST event we ever see for a call."""
    call_id = await repo.upsert_call(
        db,
        origin="webhook",
        room_sid="RM_late",
        ended_at_ms=1_800_000_000_000,
        end_reason="SIP_TRUNK_FAILURE",
    )
    row = await repo.get_call(db, call_id)
    assert row is not None
    assert row.room_sid == "RM_late"
    assert row.end_reason == "SIP_TRUNK_FAILURE"
    assert row.started_at_ms is None  # never observed, not faked as 0


async def test_events_for_one_room_sid_merge_into_one_row(db: AsyncSession) -> None:
    first = await repo.upsert_call(
        db, origin="webhook", room_sid="RM_1", room_name="call-1", started_at_ms=1000
    )
    second = await repo.upsert_call(
        db, origin="webhook", room_sid="RM_1", ended_at_ms=5000, end_reason="CLIENT"
    )
    assert first == second
    assert await _count(db, "calls") == 1


async def test_a_key_is_required(db: AsyncSession) -> None:
    """An event with neither key cannot be deduplicated, so it is refused."""
    with pytest.raises(ValueError, match="room_sid or attempt_id"):
        await repo.upsert_call(db, origin="webhook", room_name="only-a-name")


# --- room_name is best-effort, never the key --------------------------------


async def test_two_calls_with_no_room_name_stay_two_rows(db: AsyncSession) -> None:
    """A pinned/absent room name must not collapse concurrent calls."""
    a = await repo.upsert_call(db, origin="webhook", room_sid="RM_a")
    b = await repo.upsert_call(db, origin="webhook", room_sid="RM_b")
    assert a != b
    assert await _count(db, "calls") == 2


async def test_two_calls_sharing_one_room_name_stay_two_rows(db: AsyncSession) -> None:
    a = await repo.upsert_call(db, origin="webhook", room_sid="RM_a", room_name="fixed")
    b = await repo.upsert_call(db, origin="webhook", room_sid="RM_b", room_name="fixed")
    assert a != b
    assert await _count(db, "calls") == 2


async def test_failed_invites_with_no_room_are_separate_rows(db: AsyncSession) -> None:
    """A 503 on INVITE never creates a room: room_sid stays NULL for both, and
    the nullable-unique column must not merge them."""
    a = await repo.upsert_call(db, origin="loadgen", attempt_id="att-1", run_id="run-1")
    b = await repo.upsert_call(db, origin="loadgen", attempt_id="att-2", run_id="run-1")
    assert a != b
    assert await _count(db, "calls") == 2
    rows = await repo.list_calls(db, is_probe=None)
    assert [r.room_sid for r in rows] == [None, None]


# --- merge semantics --------------------------------------------------------


async def test_loadgen_attempt_is_merged_by_the_later_webhook(db: AsyncSession) -> None:
    """The load generator places the call; the webhook names the room."""
    placed = await repo.upsert_call(
        db,
        origin="loadgen",
        attempt_id="att-9",
        run_id="run-9",
        started_at_ms=1000,
        is_probe=True,
    )
    observed = await repo.upsert_call(
        db,
        origin="webhook",
        room_sid="RM_9",
        attempt_id="att-9",
        room_name="load-9",
        ended_at_ms=4000,
    )
    assert placed == observed
    assert await _count(db, "calls") == 1
    row = await repo.get_call(db, placed)
    assert row is not None
    assert row.room_sid == "RM_9"
    assert row.room_name == "load-9"
    # origin records the writer that created the row, so the enriched attempt is
    # still a loadgen call.
    assert row.origin == "loadgen"
    assert row.is_probe == 1


async def test_a_later_event_never_clobbers_a_known_value(db: AsyncSession) -> None:
    """None means "this event did not say", not "set it back to unknown"."""
    call_id = await repo.upsert_call(
        db,
        origin="webhook",
        room_sid="RM_2",
        room_name="named",
        agent_id="agent-7",
        tenant_id="acme",
    )
    await repo.upsert_call(db, origin="webhook", room_sid="RM_2", end_reason="CLIENT")
    row = await repo.get_call(db, call_id)
    assert row is not None
    assert row.room_name == "named"
    assert row.agent_id == "agent-7"
    assert row.tenant_id == "acme"
    assert row.end_reason == "CLIENT"


async def test_room_sid_is_never_repointed(db: AsyncSession) -> None:
    """Filling a NULL key is a merge; overwriting a set one would re-point the
    row at a different call."""
    call_id = await repo.upsert_call(db, origin="webhook", room_sid="RM_3")
    await repo.upsert_call(db, origin="webhook", room_sid="RM_3", attempt_id="att-3")
    row = await repo.get_call(db, call_id)
    assert row is not None
    assert row.room_sid == "RM_3"
    assert row.attempt_id == "att-3"


async def test_a_taken_attempt_id_does_not_lose_the_event(db: AsyncSession) -> None:
    """The webhook and the load generator can each create a row before either
    learns the other's key. The room_sid row wins the merge; the event's other
    fields still land, and nothing raises or is deleted."""
    by_room = await repo.upsert_call(db, origin="webhook", room_sid="RM_4")
    by_attempt = await repo.upsert_call(db, origin="loadgen", attempt_id="att-4")
    assert by_room != by_attempt

    merged = await repo.upsert_call(
        db,
        origin="webhook",
        room_sid="RM_4",
        attempt_id="att-4",
        room_name="room-4",
    )
    assert merged == by_room
    room_row = await repo.get_call(db, by_room)
    attempt_row = await repo.get_call(db, by_attempt)
    assert room_row is not None and attempt_row is not None
    assert room_row.room_name == "room-4"  # the non-key field still landed
    assert room_row.attempt_id is None  # the taken key was not stolen
    assert attempt_row.attempt_id == "att-4"  # the other row is untouched
    assert await _count(db, "calls") == 2


# --- derived columns --------------------------------------------------------


async def test_timestamps_converge_when_events_arrive_out_of_order(
    db: AsyncSession,
) -> None:
    call_id = await repo.upsert_call(
        db, origin="webhook", room_sid="RM_5", started_at_ms=5000, ended_at_ms=6000
    )
    await repo.upsert_call(
        db, origin="webhook", room_sid="RM_5", started_at_ms=4000, ended_at_ms=5500
    )
    row = await repo.get_call(db, call_id)
    assert row is not None
    assert row.started_at_ms == 4000  # earliest start wins
    assert row.ended_at_ms == 6000  # latest end wins
    assert row.duration_ms == 2000


async def test_duration_stays_null_when_the_clocks_disagree(db: AsyncSession) -> None:
    """A negative span means two clocks disagree: publish NULL, not 0."""
    call_id = await repo.upsert_call(
        db, origin="agent", room_sid="RM_6", started_at_ms=9000
    )
    await repo.upsert_call(db, origin="agent", room_sid="RM_6", ended_at_ms=1000)
    row = await repo.get_call(db, call_id)
    assert row is not None
    # Both ends are reported as observed; only the derived span is withheld.
    assert row.started_at_ms == 9000
    assert row.ended_at_ms == 1000
    assert row.duration_ms is None


async def test_epoch_millisecond_timestamps_round_trip(db: AsyncSession) -> None:
    """Epoch ms exceeds a 32-bit int; the columns must hold it, not wrap."""
    started = 1_800_000_000_000
    call_id = await repo.upsert_call(
        db,
        origin="webhook",
        room_sid="RM_big",
        started_at_ms=started,
        ended_at_ms=started + 12_345,
    )
    row = await repo.get_call(db, call_id)
    assert row is not None
    assert row.started_at_ms == started
    assert row.duration_ms == 12_345


# --- legs -------------------------------------------------------------------


async def test_a_redelivered_leg_updates_and_does_not_duplicate(
    db: AsyncSession,
) -> None:
    call_id = await repo.upsert_call(db, origin="webhook", room_sid="RM_7")
    await repo.upsert_call_leg(
        db, call_id=call_id, participant_sid="PA_1", kind="SIP", joined_at_ms=1000
    )
    await repo.upsert_call_leg(
        db,
        call_id=call_id,
        participant_sid="PA_1",
        left_at_ms=4000,
        disconnect_reason="USER_UNAVAILABLE",
    )
    legs = await repo.list_call_legs(db, call_id)
    assert len(legs) == 1
    assert legs[0].kind == "SIP"  # not clobbered by the second event
    assert legs[0].joined_at_ms == 1000
    assert legs[0].left_at_ms == 4000
    assert legs[0].disconnect_reason == "USER_UNAVAILABLE"


async def test_num_legs_is_derived_not_incremented(db: AsyncSession) -> None:
    call_id = await repo.upsert_call(db, origin="webhook", room_sid="RM_8")
    for sid in ("PA_1", "PA_2"):
        await repo.upsert_call_leg(db, call_id=call_id, participant_sid=sid)
    # Redeliver both: an incrementing counter would now read 4.
    for sid in ("PA_1", "PA_2"):
        await repo.upsert_call_leg(db, call_id=call_id, participant_sid=sid)
    row = await repo.get_call(db, call_id)
    assert row is not None
    assert row.num_legs == 2
    assert await _count(db, "call_legs") == 2


async def test_only_sip_attributes_are_persisted(db: AsyncSession) -> None:
    call_id = await repo.upsert_call(db, origin="webhook", room_sid="RM_attrs")
    await repo.upsert_call_leg(
        db,
        call_id=call_id,
        participant_sid="PA_sip",
        attributes={
            "sip.callID": "abc",
            "sip.trunkID": "trunk-load",
            "app.customer_secret": "leaked",
        },
    )
    legs = await repo.list_call_legs(db, call_id)
    assert legs[0].attributes_json is not None
    stored = json.loads(legs[0].attributes_json)
    assert stored == {"sip.callID": "abc", "sip.trunkID": "trunk-load"}


async def test_no_sip_attributes_stores_null_not_empty_object(
    db: AsyncSession,
) -> None:
    """"{}" would read as "we looked and there were none"; NULL is honest."""
    call_id = await repo.upsert_call(db, origin="webhook", room_sid="RM_noattrs")
    await repo.upsert_call_leg(
        db, call_id=call_id, participant_sid="PA_web", attributes={"app.x": 1}
    )
    legs = await repo.list_call_legs(db, call_id)
    assert legs[0].attributes_json is None


async def test_first_audio_track_keeps_the_earliest_publish(db: AsyncSession) -> None:
    call_id = await repo.upsert_call(db, origin="webhook", room_sid="RM_track")
    await repo.upsert_call_leg(
        db,
        call_id=call_id,
        participant_sid="PA_agent",
        first_audio_track_at_ms=3000,
        audio_track_sid="TR_1",
    )
    await repo.upsert_call_leg(
        db, call_id=call_id, participant_sid="PA_agent", first_audio_track_at_ms=5000
    )
    legs = await repo.list_call_legs(db, call_id)
    assert legs[0].first_audio_track_at_ms == 3000


async def test_legs_are_ordered_by_join_with_unjoined_last(db: AsyncSession) -> None:
    call_id = await repo.upsert_call(db, origin="webhook", room_sid="RM_order")
    await repo.upsert_call_leg(db, call_id=call_id, participant_sid="PA_late")
    await repo.upsert_call_leg(
        db, call_id=call_id, participant_sid="PA_b", joined_at_ms=2000
    )
    await repo.upsert_call_leg(
        db, call_id=call_id, participant_sid="PA_a", joined_at_ms=1000
    )
    legs = await repo.list_call_legs(db, call_id)
    assert [leg.participant_sid for leg in legs] == ["PA_a", "PA_b", "PA_late"]


async def test_is_publisher_distinguishes_false_from_unobserved(
    db: AsyncSession,
) -> None:
    call_id = await repo.upsert_call(db, origin="webhook", room_sid="RM_pub")
    await repo.upsert_call_leg(
        db, call_id=call_id, participant_sid="PA_1", is_publisher=False
    )
    await repo.upsert_call_leg(db, call_id=call_id, participant_sid="PA_2")
    legs = {leg.participant_sid: leg for leg in await repo.list_call_legs(db, call_id)}
    assert legs["PA_1"].is_publisher == 0  # observed, not publishing
    assert legs["PA_2"].is_publisher is None  # never observed


# --- reads ------------------------------------------------------------------


async def test_list_calls_excludes_load_traffic_by_default(db: AsyncSession) -> None:
    """Load traffic must not silently pollute production numbers."""
    await repo.upsert_call(db, origin="webhook", room_sid="RM_real")
    await repo.upsert_call(db, origin="loadgen", room_sid="RM_load", is_probe=True)

    real = await repo.list_calls(db)
    assert [r.room_sid for r in real] == ["RM_real"]

    probes = await repo.list_calls(db, is_probe=True)
    assert [r.room_sid for r in probes] == ["RM_load"]

    everything = await repo.list_calls(db, is_probe=None)
    assert {r.room_sid for r in everything} == {"RM_real", "RM_load"}


async def test_list_calls_orders_newest_first_with_start_less_rows_last(
    db: AsyncSession,
) -> None:
    await repo.upsert_call(db, origin="webhook", room_sid="RM_old", started_at_ms=1000)
    await repo.upsert_call(db, origin="webhook", room_sid="RM_new", started_at_ms=9000)
    await repo.upsert_call(db, origin="loadgen", attempt_id="att-503")
    rows = await repo.list_calls(db)
    assert [r.room_sid for r in rows] == ["RM_new", "RM_old", None]


async def test_list_calls_filters_by_project_and_run(db: AsyncSession) -> None:
    await repo.upsert_call(db, origin="webhook", room_sid="RM_p1", project="acme")
    await repo.upsert_call(db, origin="webhook", room_sid="RM_p2", project="other")
    await repo.upsert_call(
        db, origin="loadgen", attempt_id="a1", project="acme", run_id="run-1"
    )
    assert len(await repo.list_calls(db, project="acme")) == 2
    assert len(await repo.list_calls(db, run_id="run-1")) == 1
    assert (await repo.list_calls(db, project="other"))[0].project == "other"


async def test_default_project_when_the_event_carries_none(db: AsyncSession) -> None:
    call_id = await repo.upsert_call(db, origin="webhook", room_sid="RM_default")
    row = await repo.get_call(db, call_id)
    assert row is not None
    assert row.project == repo.DEFAULT_PROJECT


async def test_get_call_by_room_sid(db: AsyncSession) -> None:
    call_id = await repo.upsert_call(db, origin="webhook", room_sid="RM_lookup")
    found = await repo.get_call_by_room_sid(db, "RM_lookup")
    assert found is not None
    assert found.id == call_id
    assert await repo.get_call_by_room_sid(db, "RM_missing") is None


async def test_answer_latency_columns_default_to_null(db: AsyncSession) -> None:
    """The headline number is computed by a later node; an unmeasured latency is
    NULL here, never 0."""
    call_id = await repo.upsert_call(db, origin="webhook", room_sid="RM_latency")
    row = await repo.get_call(db, call_id)
    assert row is not None
    assert row.answer_latency_ms is None
    assert row.answer_latency_source is None
