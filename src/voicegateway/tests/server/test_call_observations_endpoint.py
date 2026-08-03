"""POST /v1/calls/observations: the mechanism first, then the mapping.

The tests that matter most are the mechanism ones, because the whole reason this
endpoint exists is to collect data *without* charging the reporter for it:

* the handler must return before the database write happens
  (``test_the_handler_returns_before_the_write_happens`` blocks the write and
  proves the POST still answers),
* a full queue must drop and count, never block or grow,
* one flusher for the process, not one task per request,
* the kill switch must stop the path dead,
* a failed write must not take the flusher with it.
"""

from __future__ import annotations

import asyncio
import time

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.repository import api_keys_repository as api_keys
from voicegateway.server import build_app
from voicegateway.server.api import call_observations as mod

_URL = "/v1/calls/observations"

_BASE_CONFIG = {
    "providers": {"openai": {"api_key": "test-key"}},
    "models": {"stt": {}, "llm": {}, "tts": {}},
    "projects": {},
    "fallbacks": {"stt": [], "llm": [], "tts": []},
    "cost_tracking": {"enabled": True},
}


def _write_config(tmp_path, extra: dict | None = None) -> str:
    path = tmp_path / "voicegw.yaml"
    with open(path, "w") as fh:
        yaml.dump({**_BASE_CONFIG, **(extra or {})}, fh)
    return str(path)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """No kill switch, and no ambient api key deciding whether auth applies."""
    monkeypatch.delenv("VG_DISABLE_CALL_OBSERVATIONS", raising=False)
    monkeypatch.delenv("VOICEGW_API_KEY", raising=False)


@pytest.fixture(autouse=True)
async def _stop_flusher():
    """Stop the process-wide flusher between tests.

    The flusher is bound to the test's event loop, so leaving it running would
    leak a pending task into loop teardown. Draining here is also how the queued
    items of a test become readable.
    """
    yield
    await mod.shutdown_call_observations(drain=False)


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "observations.db"))
    return Gateway(config_path=_write_config(tmp_path))


def _client(gw: Gateway) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=build_app(gw)), base_url="http://t")


async def _post(gw: Gateway, payload: dict, headers: dict | None = None):
    async with _client(gw) as client:
        return await client.post(_URL, json=payload, headers=headers)


def _observation(**overrides) -> dict:
    """An agent's report of a two-leg call: the SIP caller and the agent."""
    payload: dict = {
        "origin": "agent",
        "room_sid": "RM_obs",
        "room_name": "call-1",
        "project": "acme",
        "agent_id": "agent-1",
        "started_at_ms": 1_800_000_000_000,
        "legs": [
            {
                "participant_sid": "PA_caller",
                "identity": "sip_+15195550100",
                "kind": "SIP",
                "joined_at_ms": 1_800_000_000_100,
            },
            {
                "participant_sid": "PA_agent",
                "identity": "agent-1",
                "kind": "AGENT",
                "joined_at_ms": 1_800_000_001_000,
                "first_audio_track_at_ms": 1_800_000_003_842,
                "audio_track_sid": "TR_1",
                "audio_codec": "audio/opus",
            },
        ],
    }
    payload.update(overrides)
    return payload


async def _await_flushed(count: int, *, timeout: float = 5.0) -> None:
    """Wait until ``count`` reports have been written (or the deadline passes)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        stats = mod.call_observation_stats()
        if int(stats["written_total"]) >= count and stats["queue_depth"] == 0:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"only flushed {mod.call_observation_stats()}")


async def _calls(gw: Gateway) -> list[dict]:
    return await gw.storage.list_calls(limit=50, is_probe=None)


# --- the mechanism ----------------------------------------------------------


async def test_the_handler_returns_before_the_write_happens(gateway, monkeypatch):
    """The whole point: the reporter is not charged for the write.

    ``upsert_call`` is gated on an event that is still unset when the POST
    returns, so this cannot pass by being merely fast -- it can only pass if the
    handler never awaited the write at all.
    """
    gate = asyncio.Event()
    real_upsert = gateway.storage.upsert_call

    async def _blocked_upsert(**kwargs):
        await gate.wait()
        return await real_upsert(**kwargs)

    monkeypatch.setattr(gateway.storage, "upsert_call", _blocked_upsert)

    started = time.perf_counter()
    resp = await _post(gateway, _observation())
    elapsed = time.perf_counter() - started

    assert resp.status_code == 202
    assert resp.json()["status"] == "queued"
    # The write is still blocked, and nothing is in the table yet.
    assert not gate.is_set()
    assert await _calls(gateway) == []
    assert mod.call_observation_stats()["written_total"] == 0
    assert elapsed < 1.0

    gate.set()
    await _await_flushed(1)
    assert len(await _calls(gateway)) == 1


async def test_a_full_queue_drops_and_counts_instead_of_blocking(gateway, monkeypatch):
    """Bounded, with an observable drop counter. Never block, never grow."""
    monkeypatch.setattr(mod, "_QUEUE_MAXSIZE", 2)
    gate = asyncio.Event()
    real_upsert = gateway.storage.upsert_call

    async def _blocked_upsert(**kwargs):
        await gate.wait()
        return await real_upsert(**kwargs)

    monkeypatch.setattr(gateway.storage, "upsert_call", _blocked_upsert)

    posted = 4
    async with _client(gateway) as client:
        responses = [
            await client.post(_URL, json=_observation(room_sid=f"RM_{i}"))
            for i in range(posted)
        ]

    statuses = [r.status_code for r in responses]
    # The flusher holds at most one item in flight and the queue holds two, so
    # posting four cannot fit; how many fit depends on when the flusher got its
    # first item, which is why this asserts the invariant, not an exact split.
    assert 429 in statuses
    assert responses[-1].status_code == 429
    dropped = int(responses[-1].json()["dropped_total"])
    assert dropped >= 1
    assert responses[-1].json()["status"] == "dropped"
    assert int(responses[-1].json()["queue_depth"]) <= 2

    gate.set()
    await _await_flushed(posted - dropped)
    stats = mod.call_observation_stats()
    # Nothing vanished unaccounted for: every POST was either written or counted.
    assert int(stats["written_total"]) + int(stats["dropped_total"]) == posted
    assert len(await _calls(gateway)) == posted - dropped


async def test_only_one_flusher_runs_for_many_reports(gateway):
    """One background flusher, not one task per request."""
    async with _client(gateway) as client:
        for i in range(5):
            resp = await client.post(_URL, json=_observation(room_sid=f"RM_one_{i}"))
            assert resp.status_code == 202

    flushers = [
        t for t in asyncio.all_tasks() if t.get_name() == mod._FLUSHER_TASK_NAME
    ]
    assert len(flushers) == 1
    assert mod.call_observation_stats()["flusher_running"] is True

    await _await_flushed(5)
    assert len(await _calls(gateway)) == 5


async def test_a_failed_write_does_not_stop_the_flusher(gateway, monkeypatch):
    """One unwritable report must not silence every report after it."""
    real_upsert = gateway.storage.upsert_call
    calls: list[str] = []

    async def _flaky_upsert(**kwargs):
        calls.append(str(kwargs.get("room_sid")))
        if len(calls) == 1:
            raise RuntimeError("database is busy")
        return await real_upsert(**kwargs)

    monkeypatch.setattr(gateway.storage, "upsert_call", _flaky_upsert)

    async with _client(gateway) as client:
        await client.post(_URL, json=_observation(room_sid="RM_boom"))
        await client.post(_URL, json=_observation(room_sid="RM_fine"))

    await _await_flushed(1)
    stats = mod.call_observation_stats()
    assert stats["failed_total"] == 1
    assert stats["written_total"] == 1
    assert stats["flusher_running"] is True
    assert [c["room_sid"] for c in await _calls(gateway)] == ["RM_fine"]


async def test_shutdown_drains_what_was_accepted(gateway):
    """A 202 is a promise: the queue is written out on shutdown, not discarded."""
    async with _client(gateway) as client:
        for i in range(3):
            assert (
                await client.post(_URL, json=_observation(room_sid=f"RM_drain_{i}"))
            ).status_code == 202

    await mod.shutdown_call_observations()

    assert len(await _calls(gateway)) == 3
    # The flusher is stopped and the state is gone.
    assert mod.call_observation_stats() == {
        "queue_depth": 0,
        "dropped_total": 0,
        "written_total": 0,
        "failed_total": 0,
        "flusher_running": False,
    }
    assert [
        t for t in asyncio.all_tasks() if t.get_name() == mod._FLUSHER_TASK_NAME
    ] == []


async def test_shutdown_is_a_noop_when_nothing_was_ever_reported():
    await mod.shutdown_call_observations()
    await mod.shutdown_call_observations()
    assert mod.call_observation_stats()["flusher_running"] is False


# --- the kill switch --------------------------------------------------------


async def test_kill_switch_disables_the_whole_path(gateway, monkeypatch):
    monkeypatch.setenv("VG_DISABLE_CALL_OBSERVATIONS", "1")

    resp = await _post(gateway, _observation())

    assert resp.status_code == 503
    assert "VG_DISABLE_CALL_OBSERVATIONS" in resp.json()["detail"]
    # Nothing enqueued, no flusher started, nothing written.
    assert mod.call_observation_stats() == {
        "queue_depth": 0,
        "dropped_total": 0,
        "written_total": 0,
        "failed_total": 0,
        "flusher_running": False,
    }
    assert await _calls(gateway) == []


async def test_kill_switch_set_to_a_falsy_word_leaves_the_path_on(gateway, monkeypatch):
    monkeypatch.setenv("VG_DISABLE_CALL_OBSERVATIONS", "0")

    resp = await _post(gateway, _observation())

    assert resp.status_code == 202
    await _await_flushed(1)
    assert len(await _calls(gateway)) == 1


async def test_kill_switch_takes_effect_without_a_restart(gateway, monkeypatch):
    """Read per request, so an operator can switch it off mid-incident."""
    assert (await _post(gateway, _observation())).status_code == 202
    monkeypatch.setenv("VG_DISABLE_CALL_OBSERVATIONS", "true")
    assert (await _post(gateway, _observation(room_sid="RM_after"))).status_code == 503


# --- auth -------------------------------------------------------------------


async def test_auth_is_required_when_api_keys_are_configured(tmp_path, monkeypatch):
    """Router-level require_scope("write"): a handler cannot forget to opt in."""
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "observations-auth.db"))
    config = _write_config(
        tmp_path, {"auth": {"api_keys": [{"name": "agent", "token": "sk-configured"}]}}
    )
    gw = Gateway(config_path=config)

    assert (await _post(gw, _observation())).status_code == 401
    assert (
        await _post(gw, _observation(), {"Authorization": "Bearer wrong"})
    ).status_code == 401
    assert await _calls(gw) == []

    ok = await _post(gw, _observation(), {"Authorization": "Bearer sk-configured"})
    assert ok.status_code == 202
    await _await_flushed(1)
    assert len(await _calls(gw)) == 1


async def test_a_key_without_the_write_scope_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "observations-scope.db"))
    config = _write_config(
        tmp_path,
        {
            "auth": {
                "api_keys": [{"name": "reader", "token": "sk-read", "scopes": ["read"]}]
            }
        },
    )
    gw = Gateway(config_path=config)

    resp = await _post(gw, _observation(), {"Authorization": "Bearer sk-read"})

    assert resp.status_code == 403
    assert await _calls(gw) == []


async def test_tenant_is_stamped_from_the_key_not_the_payload(gateway):
    """The tenant travels on the queued item, resolved from the verified key."""
    await gateway.storage._ensure_initialized()
    async with gateway.storage._conn.session() as db:
        created = await api_keys.create_api_key(db, name="agent", tenant_id="acme")

    resp = await _post(
        gateway, _observation(), {"Authorization": f"Bearer {created.plaintext}"}
    )

    assert resp.status_code == 202
    await _await_flushed(1)
    rows = await _calls(gateway)
    assert [r["tenant_id"] for r in rows] == ["acme"]


# --- what is accepted, and what is refused rather than ignored --------------


async def test_the_report_is_merged_into_calls_and_legs(gateway):
    resp = await _post(gateway, _observation())
    assert resp.status_code == 202
    await _await_flushed(1)

    rows = await _calls(gateway)
    assert len(rows) == 1
    call = rows[0]
    assert call["room_sid"] == "RM_obs"
    assert call["room_name"] == "call-1"
    assert call["origin"] == "agent"
    assert call["project"] == "acme"
    assert call["agent_id"] == "agent-1"
    assert call["started_at_ms"] == 1_800_000_000_000
    # Derived from the reported legs by the webhook's own rule.
    assert call["channel"] == "sip"
    assert call["num_legs"] == 2
    # Still not invented here: calls_repository owns the one computation. This
    # endpoint contributes the two timestamps it subtracts, and once T5 landed
    # those timestamps do resolve to a number (3_842 - 100). The source is
    # "agent_report" rather than "webhook_proxy" because the legs are stamped
    # source=obs.origin, so the derivation knows both came from one in-process
    # millisecond clock instead of a webhook's whole-second created_at.
    assert call["answer_latency_ms"] == 3742
    assert call["answer_latency_source"] == "agent_report"

    legs = await gateway.storage.list_call_legs(call["id"])
    assert [leg["participant_sid"] for leg in legs] == ["PA_caller", "PA_agent"]
    caller, agent = legs
    assert caller["kind"] == "SIP"
    assert caller["joined_at_ms"] == 1_800_000_000_100
    # The two inputs T5's computation needs, at the agent's own ms precision.
    assert agent["first_audio_track_at_ms"] == 1_800_000_003_842
    assert agent["audio_track_sid"] == "TR_1"
    assert agent["audio_codec"] == "audio/opus"


async def test_a_report_merges_into_a_call_the_webhook_already_created(gateway):
    """Same rows, same repository: the self-report enriches, it does not fork."""
    await gateway.storage.upsert_call(
        origin="webhook", room_sid="RM_obs", started_at_ms=1_800_000_000_000
    )

    await _post(gateway, _observation())
    await _await_flushed(1)

    rows = await _calls(gateway)
    assert len(rows) == 1
    # origin records the writer that created the row and is not rewritten.
    assert rows[0]["origin"] == "webhook"
    assert rows[0]["num_legs"] == 2


async def test_a_sip_legs_disconnect_reason_becomes_the_calls_end_reason(gateway):
    await _post(
        gateway,
        _observation(
            legs=[
                {
                    "participant_sid": "PA_caller",
                    "kind": "SIP",
                    "left_at_ms": 1_800_000_009_000,
                    "disconnect_reason": "CLIENT_INITIATED",
                }
            ]
        ),
    )
    await _await_flushed(1)

    rows = await _calls(gateway)
    assert rows[0]["end_reason"] == "CLIENT_INITIATED"


async def test_an_agent_legs_disconnect_reason_stays_on_its_own_leg(gateway):
    """Same rule as the webhook: the call ended as the *caller* experienced it."""
    await _post(
        gateway,
        _observation(
            legs=[
                {
                    "participant_sid": "PA_agent",
                    "kind": "AGENT",
                    "left_at_ms": 1_800_000_009_000,
                    "disconnect_reason": "CLIENT_INITIATED",
                }
            ]
        ),
    )
    await _await_flushed(1)

    rows = await _calls(gateway)
    assert rows[0]["end_reason"] is None
    assert rows[0]["channel"] is None
    legs = await gateway.storage.list_call_legs(rows[0]["id"])
    assert legs[0]["disconnect_reason"] == "CLIENT_INITIATED"


async def test_a_load_worker_reports_an_attempt_that_never_became_a_room(gateway):
    """A 503 on INVITE creates no room, and that is the row that matters most."""
    resp = await _post(
        gateway,
        {
            "origin": "loadgen",
            "attempt_id": "att-1",
            "run_id": "run-1",
            "started_at_ms": 1_800_000_000_000,
        },
    )
    assert resp.status_code == 202
    await _await_flushed(1)

    rows = await _calls(gateway)
    assert len(rows) == 1
    assert rows[0]["room_sid"] is None
    assert rows[0]["attempt_id"] == "att-1"
    assert rows[0]["run_id"] == "run-1"
    assert rows[0]["origin"] == "loadgen"


async def test_a_report_with_no_correlation_key_is_refused(gateway):
    """422 at the edge, not a 202 followed by a silent drop in the flusher."""
    resp = await _post(gateway, {"origin": "agent", "room_name": "only-a-name"})

    assert resp.status_code == 422
    assert mod.call_observation_stats()["flusher_running"] is False
    assert await _calls(gateway) == []


async def test_is_probe_cannot_be_set_from_the_wire(gateway):
    """D0 decision 1: a forged probe flag would hide real traffic."""
    resp = await _post(gateway, _observation(is_probe=True))

    assert resp.status_code == 422
    assert await _calls(gateway) == []


async def test_tenant_id_is_not_accepted_from_the_payload(gateway):
    resp = await _post(gateway, _observation(tenant_id="someone-else"))

    assert resp.status_code == 422
    assert await _calls(gateway) == []


@pytest.mark.parametrize(
    "unstorable",
    [
        {"loss_pct": 0.0},
        {"jitter_ms": 12},
        {"mos": 4.1},
        {"sip_response_code": 503},
        {"answer_latency_ms": 4100},
        {"subscribe_latency_ms": 120},
    ],
)
async def test_a_field_with_no_column_is_refused_not_ignored(gateway, unstorable):
    """Accepting these silently would let a reporter believe they were stored.

    Per-call loss/jitter/MOS and SIP response codes have no column by decision;
    a reported answer-latency *duration* has no column in the M1 schema, and
    writing ``answer_latency_ms`` here would be T5's precedence rule.
    """
    resp = await _post(gateway, _observation(**unstorable))

    assert resp.status_code == 422
    assert await _calls(gateway) == []


@pytest.mark.parametrize(
    "bad_timestamp",
    [1_800_000_000, 1_800_000_000_000_000],
)
async def test_a_timestamp_in_the_wrong_unit_is_refused(gateway, bad_timestamp):
    """Seconds or microseconds instead of milliseconds is the likeliest bug.

    Merged as epoch ms, a seconds-scale value would put the call in 1970 and
    publish a ~55-year duration.
    """
    resp = await _post(gateway, _observation(started_at_ms=bad_timestamp))

    assert resp.status_code == 422
    assert await _calls(gateway) == []


async def test_an_origin_of_webhook_is_refused(gateway):
    """Only the signature-verified receiver may write an event as a webhook."""
    resp = await _post(gateway, _observation(origin="webhook"))

    assert resp.status_code == 422
    assert await _calls(gateway) == []


async def test_a_leg_without_a_participant_sid_is_refused(gateway):
    """participant_sid is half the leg's unique key and is never NULL."""
    resp = await _post(gateway, _observation(legs=[{"identity": "agent-1"}]))

    assert resp.status_code == 422
    assert await _calls(gateway) == []


async def test_more_legs_than_the_cap_is_refused(gateway):
    legs = [{"participant_sid": f"PA_{i}"} for i in range(mod._MAX_LEGS + 1)]

    resp = await _post(gateway, _observation(legs=legs))

    assert resp.status_code == 422
    assert await _calls(gateway) == []


async def test_a_redelivered_report_does_not_duplicate_the_call_or_legs(gateway):
    payload = _observation()
    async with _client(gateway) as client:
        for _ in range(3):
            assert (await client.post(_URL, json=payload)).status_code == 202

    await _await_flushed(3)
    rows = await _calls(gateway)
    assert len(rows) == 1
    assert rows[0]["num_legs"] == 2


async def test_no_storage_on_this_collector_is_a_503(tmp_path, monkeypatch):
    # VOICEGW_DB_PATH turns storage on regardless of the config, so the
    # storage-disabled shape needs it unset (same as test_dashboard_storage_none).
    monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)
    gw = Gateway(
        config_path=_write_config(tmp_path, {"cost_tracking": {"enabled": False}})
    )
    assert gw.storage is None

    resp = await _post(gw, _observation())

    assert resp.status_code == 503
    assert mod.call_observation_stats()["flusher_running"] is False
