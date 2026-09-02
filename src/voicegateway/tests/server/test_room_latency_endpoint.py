"""GET /v1/rooms/{room}/latency: the component split, over HTTP.

The split was only ever readable in-process, by a prober sharing the agent's
local store. A deployment running the agent and the collector in separate
containers holds every row needed and could not reach the computation. This
endpoint is that computation, reachable; these tests pin the parts a consumer
would otherwise have to trust.

**Four answers that must stay distinct.** 404 (never seen this room), 200 with
``components: null`` (room known, nothing correlated), ``complete: false`` (a
real split, still mid-flush, poll again) and a null component (that stage
produced no measurement). The consumer renders a different state for each, so
collapsing any pair loses information it needs.

**Nothing is ever rendered as a fabricated zero.** These numbers go into a
document somebody reads months later, and a stage shown as ``0.00`` reads as
"instant".
"""

from __future__ import annotations

import os
import tempfile
import time
from typing import Any

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.inference.session.context import reset_tenant_id, set_tenant
from voicegateway.livekit_diag.latency import LatencyResult, summarize
from voicegateway.middleware.turn_tracker_middleware import TurnRow
from voicegateway.models.request_model import RequestRecord
from voicegateway.repository import api_keys_repository as api_keys
from voicegateway.repository import turns_repository as turns
from voicegateway.server import build_app

ROOM = "demo-a1b2c3"
SESSION = "sess-demo-1"


def _req(
    room: str,
    session_id: str,
    modality: str,
    *,
    ttfb_ms: float | None = None,
    eou: dict[str, Any] | None = None,
    project: str = "acme-w1",
) -> RequestRecord:
    """One request row as ``attach`` writes it: the room lives on metadata."""
    metadata: dict[str, Any] = {"room": room}
    if eou is not None:
        metadata["eou"] = eou
    return RequestRecord(
        id=f"req-{room}-{modality}-{session_id}-{time.time_ns()}",
        timestamp=time.time(),
        project=project,
        modality=modality,
        model_id=f"test/{modality}",
        provider="test",
        input_units=0,
        output_units=0,
        cost_usd=0.0,
        pricing_source="test",
        ttfb_ms=ttfb_ms,
        total_latency_ms=None,
        status="success",
        fallback_from=None,
        error_message=None,
        metadata=metadata,
        session_id=session_id,
    )


class _Harness:
    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        tmp = self._tmp.name
        self._prev_db_path = os.environ.get("VOICEGW_DB_PATH")
        os.environ["VOICEGW_DB_PATH"] = os.path.join(tmp, "rooms.db")
        cfg = {
            "providers": {"openai": {"api_key": "test-key"}},
            "models": {"stt": {}, "llm": {}, "tts": {}},
            "projects": {},
            "fallbacks": {"stt": [], "llm": [], "tts": []},
            "cost_tracking": {"enabled": True},
        }
        cfg_path = os.path.join(tmp, "voicegw.yaml")
        with open(cfg_path, "w") as f:
            yaml.dump(cfg, f)
        self.gateway = Gateway(config_path=cfg_path)
        self.app = build_app(self.gateway, enable_mcp_sse=False, enable_dashboard=False)
        self.app.state.ch_client = None

    def client(self) -> AsyncClient:
        return AsyncClient(
            transport=ASGITransport(app=self.app), base_url="http://test"
        )

    def cleanup(self) -> None:
        if self._prev_db_path is None:
            os.environ.pop("VOICEGW_DB_PATH", None)
        else:
            os.environ["VOICEGW_DB_PATH"] = self._prev_db_path
        self._tmp.cleanup()


async def _seed_complete(storage, room: str = ROOM, session: str = SESSION) -> None:
    """A finished turn: turn detection, STT head/tail, LLM ttft and TTS ttfb."""
    await storage.log_request(
        _req(
            room,
            session,
            "eou",
            eou={
                "end_of_utterance_delay": 0.302,
                "transcription_delay": 0.088,
                "time_to_first_partial_ms": 121.0,
            },
        )
    )
    await storage.log_request(_req(room, session, "stt", ttfb_ms=209.0))
    await storage.log_request(_req(room, session, "llm", ttfb_ms=447.0))
    await storage.log_request(_req(room, session, "tts", ttfb_ms=92.0))


async def _seed_turns(storage, session: str, speeds: list[int | None]) -> None:
    rows = [
        TurnRow(
            session_id=session,
            turn_index=i,
            caller_speak_start_ms=1_800_000_000_000 + i * 5000,
            caller_speak_end_ms=1_800_000_002_000 + i * 5000,
            agent_speak_start_ms=(
                None if s is None else 1_800_000_002_000 + i * 5000 + s
            ),
            agent_speak_end_ms=None,
            response_speed_ms=s,
        )
        for i, s in enumerate(speeds)
    ]
    async with storage._conn.session() as db:
        await turns.create_turns_bulk(db, rows, tenant_id=None)


@pytest.fixture
async def harness():
    h = _Harness()
    await h.gateway.storage._ensure_initialized()
    reset_tenant_id()
    try:
        yield h
    finally:
        reset_tenant_id()
        h.cleanup()


async def _make_key(gateway, *, tenant_id=None, role="tenant"):
    async with gateway.storage._conn.session() as db:
        created = await api_keys.create_api_key(
            db, name=f"k-{tenant_id}-{role}", tenant_id=tenant_id, role=role
        )
    return created.plaintext


async def _get(harness, path: str, key: str | None = None):
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    async with harness.client() as c:
        return await c.get(path, headers=headers)


# --------------------------------------------------------------------------
# The split
# --------------------------------------------------------------------------


async def test_a_complete_split_returns_every_component(harness):
    """The headline case, in milliseconds, with complete=true."""
    await _seed_complete(harness.gateway.storage)
    r = await _get(harness, f"/v1/rooms/{ROOM}/latency")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["room"] == ROOM
    assert body["project"] == "acme-w1"
    assert body["complete"] is True
    c = body["components"]
    # aggregate_components works in SECONDS; the wire is milliseconds.
    assert c["eou_ms"] == pytest.approx(302.0)
    assert c["stt_ttfp_ms"] == pytest.approx(121.0)
    assert c["stt_transcription_delay_ms"] == pytest.approx(88.0)
    assert c["stt_ms"] == pytest.approx(209.0)
    assert c["llm_ttft_ms"] == pytest.approx(447.0)
    assert c["tts_ttfb_ms"] == pytest.approx(92.0)


async def test_a_mid_flush_split_is_incomplete_and_zeroes_nothing(harness):
    """The agent is still writing: LLM landed, TTS has not.

    ``complete: false`` is what makes polling safe, and the missing leg must be
    null. Rendering it as 0.0 would read as "TTS was instant", which is the
    false claim this endpoint exists to avoid shipping.
    """
    storage = harness.gateway.storage
    await storage.log_request(_req(ROOM, SESSION, "llm", ttfb_ms=447.0))

    body = (await _get(harness, f"/v1/rooms/{ROOM}/latency")).json()
    assert body["complete"] is False
    assert body["components"]["llm_ttft_ms"] == pytest.approx(447.0)
    assert body["components"]["tts_ttfb_ms"] is None
    assert body["components"]["eou_ms"] is None


@pytest.mark.parametrize(
    "field",
    [
        "eou_ms",
        "stt_ttfp_ms",
        "stt_transcription_delay_ms",
        "stt_ms",
        "tts_ttfb_ms",
    ],
)
async def test_an_unproduced_component_is_null_not_zero(harness, field):
    """Present and NULL. "not 0.0" alone would pass on any garbage value.

    ``llm_ttft_ms`` is excluded because this fixture measures it, and asserted
    separately below, so the parametrisation cannot go green by measuring
    nothing at all.
    """
    await harness.gateway.storage.log_request(_req(ROOM, SESSION, "llm", ttfb_ms=1.0))
    c = (await _get(harness, f"/v1/rooms/{ROOM}/latency")).json()["components"]
    assert field in c, (
        "the field must be present so a consumer can render a fixed strip"
    )
    assert c[field] is None


async def test_the_one_measured_component_really_did_measure(harness):
    """The non-vacuous half of the test above."""
    await harness.gateway.storage.log_request(_req(ROOM, SESSION, "llm", ttfb_ms=1.0))
    c = (await _get(harness, f"/v1/rooms/{ROOM}/latency")).json()["components"]
    assert c["llm_ttft_ms"] == pytest.approx(1.0)


# --------------------------------------------------------------------------
# 404 and 200-with-nulls are different claims
# --------------------------------------------------------------------------


async def test_an_unknown_room_is_404(harness):
    """This collector has never seen it."""
    assert (await _get(harness, "/v1/rooms/never-existed/latency")).status_code == 404


async def test_a_known_room_with_nothing_correlated_is_200_with_nulls(harness):
    """The room exists and produced no usable latency: an uninstrumented agent.

    Deliberately NOT a 404. The consumer shows "connected, waiting" here and
    "unknown room" for a 404, so collapsing the two would lose a real state.
    """
    await harness.gateway.storage.log_request(_req(ROOM, SESSION, "stt"))
    r = await _get(harness, f"/v1/rooms/{ROOM}/latency")
    assert r.status_code == 200
    body = r.json()
    assert body["components"] is None
    assert body["complete"] is False
    assert body["e2e_ms"] is None
    assert body["turn_count"] == 0


# --------------------------------------------------------------------------
# Tenancy
# --------------------------------------------------------------------------


async def test_a_foreign_tenants_room_is_404_not_403(harness):
    """A 403 would confirm the room exists, which is the fact being protected."""
    storage = harness.gateway.storage
    set_tenant("acme")
    await _seed_complete(storage)
    reset_tenant_id()

    beta_key = await _make_key(harness.gateway, tenant_id="beta")
    assert (
        await _get(harness, f"/v1/rooms/{ROOM}/latency", beta_key)
    ).status_code == 404

    # Non-vacuous: the owner sees it.
    acme_key = await _make_key(harness.gateway, tenant_id="acme")
    assert (
        await _get(harness, f"/v1/rooms/{ROOM}/latency", acme_key)
    ).status_code == 200


# --------------------------------------------------------------------------
# since_turn is a cursor over turns and nothing else
# --------------------------------------------------------------------------


async def test_since_turn_filters_turns_but_never_the_aggregates(harness):
    """The property a polling consumer cannot check for itself.

    If the cursor narrowed ``components`` or ``e2e_ms``, every poll would
    disagree with the one before it and the report would drift as it refreshed.
    """
    storage = harness.gateway.storage
    await _seed_complete(storage)
    await _seed_turns(storage, SESSION, [900, 1000, 1100, 1200])

    whole = (await _get(harness, f"/v1/rooms/{ROOM}/latency")).json()
    tail = (await _get(harness, f"/v1/rooms/{ROOM}/latency?since_turn=2")).json()

    assert [t["turn_index"] for t in whole["turns"]] == [0, 1, 2, 3]
    assert [t["turn_index"] for t in tail["turns"]] == [3]

    assert tail["components"] == whole["components"]
    assert tail["e2e_ms"] == whole["e2e_ms"]
    # turn_count covers the whole call too, so a cursored poll still knows what
    # the aggregates are over.
    assert tail["turn_count"] == whole["turn_count"] == 4


# --------------------------------------------------------------------------
# The endpoint and the CLI cannot drift
# --------------------------------------------------------------------------


async def test_e2e_percentiles_match_summarize_over_the_same_inputs(harness):
    """Computed BY ``summarize``, so p95 means one thing across both surfaces."""
    storage = harness.gateway.storage
    speeds = [902, 1000, 1041, 1074, 1200, 1288, 1301]
    await _seed_complete(storage)
    await _seed_turns(storage, SESSION, speeds)

    got = (await _get(harness, f"/v1/rooms/{ROOM}/latency")).json()["e2e_ms"]
    want = summarize(LatencyResult(agent="", e2e_samples=[float(s) for s in speeds]))

    assert got["p50"] == want["p50"]
    assert got["p95"] == want["p95"]
    assert got["avg"] == want["avg"]
    assert got["min"] == want["min"]
    assert got["max"] == want["max"]
    # The wire calls it n; summarize calls it trials.
    assert got["n"] == want["trials"] == len(speeds)


async def test_a_turn_that_never_got_a_reply_is_excluded_not_zeroed(harness):
    """``response_speed_ms`` is null when the agent never spoke for that turn.

    Averaging a null in as 0 would drag every percentile toward zero and report
    a call as faster than it was.
    """
    storage = harness.gateway.storage
    await _seed_complete(storage)
    await _seed_turns(storage, SESSION, [1000, None, 1200])

    body = (await _get(harness, f"/v1/rooms/{ROOM}/latency")).json()
    assert body["turn_count"] == 3
    assert body["e2e_ms"]["n"] == 2
    assert body["e2e_ms"]["min"] == 1000.0
    # The unanswered turn is still listed, with a null speed.
    speeds = [t["response_speed_ms"] for t in body["turns"]]
    assert speeds == [1000, None, 1200]


async def test_no_completed_turn_gives_a_null_block_not_a_row_of_zeros(harness):
    """``summarize`` returns zeros for an empty list, which is right for a CLI
    table and a fabricated measurement here."""
    await _seed_complete(harness.gateway.storage)
    await _seed_turns(harness.gateway.storage, SESSION, [None, None])

    body = (await _get(harness, f"/v1/rooms/{ROOM}/latency")).json()
    assert body["turn_count"] == 2
    assert body["e2e_ms"] is None


# --------------------------------------------------------------------------
# Shape
# --------------------------------------------------------------------------


async def test_it_lives_under_v1_not_the_dashboard_router(harness):
    """The consumer is a server-to-server caller holding a vk_ key, not the SPA."""
    await _seed_complete(harness.gateway.storage)
    assert (await _get(harness, f"/v1/rooms/{ROOM}/latency")).status_code == 200
    assert (await _get(harness, f"/api/rooms/{ROOM}/latency")).status_code == 404


async def test_turns_carry_the_documented_fields(harness):
    """``seq`` and ``session_id`` are here because ``turn_index`` alone cannot
    identify a turn in a room that carried more than one session."""
    await _seed_complete(harness.gateway.storage)
    await _seed_turns(harness.gateway.storage, SESSION, [1041])
    turn = (await _get(harness, f"/v1/rooms/{ROOM}/latency")).json()["turns"][0]
    assert set(turn) == {
        "seq",
        "turn_index",
        "session_id",
        "caller_speak_end_ms",
        "agent_speak_start_ms",
        "response_speed_ms",
    }


# --------------------------------------------------------------------------
# A room can carry more than one session
# --------------------------------------------------------------------------


async def test_two_sessions_in_one_room_neither_omit_nor_repeat_a_turn(harness):
    """``turn_index`` is SESSION-local, so it cannot be the cursor.

    A room carries two sessions when an agent reconnects, when two agents share
    a room, or when a probe reuses a fixed --room-name. Both sessions then count
    their turns 0, 1, 2..., so a cursor on turn_index would return two turns
    called "3" and drop one of them on the next poll. ``seq`` is room-wide.
    """
    storage = harness.gateway.storage
    await _seed_complete(storage, session="sess-a")
    await storage.log_request(_req(ROOM, "sess-b", "llm", ttfb_ms=400.0))
    # sess-b runs after sess-a, and both number their turns from zero.
    await _seed_turns(storage, "sess-a", [900, 950])
    async with storage._conn.session() as db:
        await turns.create_turns_bulk(
            db,
            [
                TurnRow(
                    session_id="sess-b",
                    turn_index=i,
                    caller_speak_start_ms=1_900_000_000_000 + i * 5000,
                    caller_speak_end_ms=1_900_000_002_000 + i * 5000,
                    agent_speak_start_ms=1_900_000_002_000 + i * 5000 + s,
                    agent_speak_end_ms=None,
                    response_speed_ms=s,
                )
                for i, s in enumerate([1100, 1150])
            ],
            tenant_id=None,
        )

    whole = (await _get(harness, f"/v1/rooms/{ROOM}/latency")).json()
    assert whole["turn_count"] == 4
    # seq is room-wide and unique; turn_index repeats across the two sessions.
    assert [t["seq"] for t in whole["turns"]] == [0, 1, 2, 3]
    assert [t["turn_index"] for t in whole["turns"]] == [0, 1, 0, 1]
    assert [t["session_id"] for t in whole["turns"]] == [
        "sess-a",
        "sess-a",
        "sess-b",
        "sess-b",
    ]
    # Chronological, so the two sessions are not interleaved.
    ends = [t["caller_speak_end_ms"] for t in whole["turns"]]
    assert ends == sorted(ends)

    # The cursor advances cleanly across the session boundary: every turn is
    # returned exactly once across successive polls, and none twice.
    seen: list[int] = []
    cursor: int | None = None
    for _ in range(4):
        q = "" if cursor is None else f"?since_turn={cursor}"
        page = (await _get(harness, f"/v1/rooms/{ROOM}/latency{q}")).json()["turns"]
        if not page:
            break
        seen.append(page[0]["seq"])
        cursor = page[0]["seq"]
    assert seen == [0, 1, 2, 3]


async def test_a_cursor_on_turn_index_would_have_been_ambiguous(harness):
    """Non-vacuous: prove turn_index really does repeat, so seq is not
    ceremony."""
    storage = harness.gateway.storage
    await _seed_complete(storage, session="sess-a")
    await storage.log_request(_req(ROOM, "sess-b", "llm", ttfb_ms=400.0))
    await _seed_turns(storage, "sess-a", [900])
    await _seed_turns(storage, "sess-b", [1100])

    indices = [
        t["turn_index"]
        for t in (await _get(harness, f"/v1/rooms/{ROOM}/latency")).json()["turns"]
    ]
    assert indices == [0, 0], "both sessions number from zero, so it repeats"


# --------------------------------------------------------------------------
# Auth enforces once keys exist
# --------------------------------------------------------------------------


async def test_no_credential_is_refused_once_api_keys_are_configured(harness):
    """The other half of the self-hosted no-op default.

    The read gate is deliberately a no-op while no API keys are configured, so
    the tests above call with no Authorization header and that is correct
    behaviour, not a gap. Once a key exists the gate enforces, and this asserts
    it does: room data must never come back unauthenticated on a deployment
    that has turned auth on.
    """
    await _seed_complete(harness.gateway.storage)
    # Before: the self-hosted default (no configured keys) serves it.
    assert (await _get(harness, f"/v1/rooms/{ROOM}/latency")).status_code == 200

    # Auth turns on when keys are CONFIGURED, which is app.state.api_keys, not
    # the presence of a minted vk_ row in the database. A non-vk_ token takes
    # the static-key path, the same way test_dashboard_api_keys sets it up.
    from voicegateway.core.auth import ApiKey

    harness.app.state.api_keys = [
        ApiKey(token="static-secret", name="ops", scopes=("read",))
    ]

    r = await _get(harness, f"/v1/rooms/{ROOM}/latency")
    assert r.status_code == 401, r.text
    assert "components" not in r.text

    # Non-vacuous: the configured key still gets the data.
    ok = await _get(harness, f"/v1/rooms/{ROOM}/latency", "static-secret")
    assert ok.status_code == 200, ok.text
    assert ok.json()["components"]["llm_ttft_ms"] == pytest.approx(447.0)
