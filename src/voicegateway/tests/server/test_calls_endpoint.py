"""GET /api/calls: the read side of the recorded call.

Two things these tests exist to hold:

1. **The payload shape is the contract.** The dashboard's layer waterfall
   (``src/dashboard/frontend/src/lib/types.ts``) is written against
   ``CallRow``/``CallLegRow`` field for field, and a renamed or dropped field
   renders as a permanently blank card that still compiles. The field lists
   below are copied from that file and frozen, so the mismatch fails here.
2. **The endpoint computes nothing and flattens nothing.** ``answer_latency_ms``
   is derived once, at write time, in ``calls_repository._derive_answer_latency``;
   what is stored is what is served, NULL included. A call that never answered
   must come back as ``null``, never ``0``.
"""

from __future__ import annotations

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.repository import api_keys_repository as api_keys
from voicegateway.server import build_app

_URL = "/api/calls"

# Copied from `CallRow` in src/dashboard/frontend/src/lib/types.ts, in order.
# The UI is already written against exactly these names.
_CALL_FIELDS = [
    "id",
    "room_sid",
    "room_name",
    "origin",
    "attempt_id",
    "run_id",
    "project",
    "tenant_id",
    "agent_id",
    "channel",
    "direction",
    "started_at_ms",
    "ended_at_ms",
    "duration_ms",
    "end_reason",
    "num_legs",
    "is_probe",
    "answer_latency_ms",
    "answer_latency_source",
]

# Copied from `CallLegRow` in the same file, in order.
_LEG_FIELDS = [
    "id",
    "call_id",
    "participant_sid",
    "identity",
    "kind",
    "region",
    "joined_at_ms",
    "left_at_ms",
    "disconnect_reason",
    "is_publisher",
    "attributes_json",
    "first_audio_track_at_ms",
    "audio_track_sid",
    "audio_codec",
    "joined_at_source",
    "first_audio_track_at_source",
]

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
    """No ambient api key deciding whether auth applies."""
    monkeypatch.delenv("VOICEGW_API_KEY", raising=False)


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "calls-endpoint.db"))
    return Gateway(config_path=_write_config(tmp_path))


@pytest.fixture
async def client(gateway):
    transport = ASGITransport(app=build_app(gateway))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _answered_call(
    storage,
    *,
    room_sid: str,
    room_name: str = "room-a",
    started_at_ms: int = 1_750_000_000_000,
    ring_ms: int = 1200,
    tenant_id: str | None = None,
    is_probe: bool = False,
    source: str | None = None,
) -> str:
    """A caller leg that joined and an agent leg that published audio.

    That pair is what makes ``_derive_answer_latency`` produce a number, so these
    rows exercise the passthrough of a non-NULL latency.
    """
    call_id = await storage.upsert_call(
        origin="webhook",
        room_sid=room_sid,
        room_name=room_name,
        started_at_ms=started_at_ms,
        tenant_id=tenant_id,
        is_probe=is_probe,
    )
    await storage.upsert_call_leg(
        call_id=call_id,
        participant_sid="PA_caller",
        kind="SIP",
        identity="sip_+15195551234",
        joined_at_ms=started_at_ms,
        attributes={"sip.phoneNumber": "+15195551234", "other": "dropped"},
        source=source,
    )
    await storage.upsert_call_leg(
        call_id=call_id,
        participant_sid="PA_agent",
        kind="AGENT",
        identity="agent-1",
        joined_at_ms=started_at_ms + 100,
        first_audio_track_at_ms=started_at_ms + ring_ms,
        source=source,
    )
    return call_id


async def _unanswered_call(
    storage, *, room_sid: str, started_at_ms: int = 1_750_000_500_000
) -> str:
    """A caller who joined and an agent that never published: NULL latency."""
    call_id = await storage.upsert_call(
        origin="webhook",
        room_sid=room_sid,
        room_name="room-dead",
        started_at_ms=started_at_ms,
    )
    await storage.upsert_call_leg(
        call_id=call_id,
        participant_sid="PA_caller_dead",
        kind="SIP",
        joined_at_ms=started_at_ms,
    )
    return call_id


# --- shape ------------------------------------------------------------------


async def test_empty_deployment_returns_an_empty_list(client):
    resp = await client.get(_URL)
    assert resp.status_code == 200
    assert resp.json() == {"calls": []}


async def test_payload_fields_match_the_frontend_types_exactly(client, gateway):
    """Field for field against types.ts: no invented, renamed or dropped key."""
    await _answered_call(gateway.storage, room_sid="RM_shape")

    body = (await client.get(_URL)).json()

    assert list(body) == ["calls"]
    (call,) = body["calls"]
    assert list(call) == [*_CALL_FIELDS, "legs"]
    for leg in call["legs"]:
        assert list(leg) == _LEG_FIELDS


async def test_legs_are_nested_under_their_call_in_join_order(client, gateway):
    await _answered_call(gateway.storage, room_sid="RM_legs")

    (call,) = (await client.get(_URL)).json()["calls"]

    assert call["num_legs"] == 2
    assert [leg["participant_sid"] for leg in call["legs"]] == [
        "PA_caller",
        "PA_agent",
    ]
    assert [leg["kind"] for leg in call["legs"]] == ["SIP", "AGENT"]
    assert all(leg["call_id"] == call["id"] for leg in call["legs"])


async def test_only_sip_attributes_are_served(client, gateway):
    """The repository stores the ``sip.*`` subset; the endpoint adds nothing."""
    await _answered_call(gateway.storage, room_sid="RM_attrs")

    (call,) = (await client.get(_URL)).json()["calls"]
    caller = call["legs"][0]

    assert caller["attributes_json"] == '{"sip.phoneNumber": "+15195551234"}'


# --- the endpoint computes nothing, and flattens nothing ---------------------


async def test_answer_latency_is_served_exactly_as_stored(client, gateway):
    await _answered_call(gateway.storage, room_sid="RM_ring", ring_ms=1200)
    stored = await gateway.storage.get_call_by_room_sid("RM_ring")

    (call,) = (await client.get(_URL)).json()["calls"]

    assert call["answer_latency_ms"] == stored["answer_latency_ms"] == 1200
    # Neither timestamp was self-reported, so the stored provenance is the
    # coarse one. The endpoint must not upgrade it.
    assert call["answer_latency_source"] == stored["answer_latency_source"]
    assert call["answer_latency_source"] == "webhook_proxy"


async def test_a_self_reported_pair_keeps_its_stronger_provenance(client, gateway):
    await _answered_call(
        gateway.storage, room_sid="RM_agent", ring_ms=830, source="agent"
    )

    (call,) = (await client.get(_URL)).json()["calls"]

    assert call["answer_latency_ms"] == 830
    assert call["answer_latency_source"] == "agent_report"
    assert [leg["joined_at_source"] for leg in call["legs"]] == ["agent", "agent"]


async def test_a_call_that_never_answered_serves_null_not_zero(client, gateway):
    """The most important row in a load test. A 0 here would report the worst
    call as the best one."""
    await _unanswered_call(gateway.storage, room_sid="RM_null")

    (call,) = (await client.get(_URL)).json()["calls"]

    assert call["answer_latency_ms"] is None
    assert call["answer_latency_source"] is None


async def test_unobserved_fields_stay_null(client, gateway):
    """No missing number becomes 0 and no missing string becomes "" on the way
    out: the UI distinguishes "not measured" from a measured zero."""
    call_id = await gateway.storage.upsert_call(
        origin="loadgen", attempt_id="attempt-1"
    )
    await gateway.storage.upsert_call_leg(
        call_id=call_id, participant_sid="PA_bare"
    )

    (call,) = (await client.get(_URL)).json()["calls"]

    for field in (
        "room_sid",
        "room_name",
        "run_id",
        "tenant_id",
        "agent_id",
        "channel",
        "direction",
        "started_at_ms",
        "ended_at_ms",
        "duration_ms",
        "end_reason",
    ):
        assert call[field] is None, field
    (leg,) = call["legs"]
    for field in (
        "identity",
        "kind",
        "region",
        "joined_at_ms",
        "left_at_ms",
        "disconnect_reason",
        "is_publisher",
        "attributes_json",
        "first_audio_track_at_ms",
        "audio_track_sid",
        "audio_codec",
        "joined_at_source",
        "first_audio_track_at_source",
    ):
        assert leg[field] is None, field


async def test_no_percentile_or_aggregate_is_added_to_the_payload(client, gateway):
    """A page of rows is not the population, so this endpoint publishes no p95
    (and no summary of any kind) for the UI to render as one."""
    for i in range(3):
        await _answered_call(
            gateway.storage,
            room_sid=f"RM_agg{i}",
            started_at_ms=1_750_000_000_000 + i * 1000,
        )

    body = (await client.get(_URL)).json()

    assert list(body) == ["calls"]


# --- ordering, bounds, and what is excluded ---------------------------------


async def test_calls_are_newest_first(client, gateway):
    await _answered_call(
        gateway.storage, room_sid="RM_old", started_at_ms=1_750_000_000_000
    )
    await _answered_call(
        gateway.storage, room_sid="RM_new", started_at_ms=1_750_000_900_000
    )

    body = (await client.get(_URL)).json()

    assert [c["room_sid"] for c in body["calls"]] == ["RM_new", "RM_old"]


async def test_limit_bounds_the_page(client, gateway):
    for i in range(3):
        await _answered_call(
            gateway.storage,
            room_sid=f"RM_lim{i}",
            started_at_ms=1_750_000_000_000 + i * 1000,
        )

    body = (await client.get(f"{_URL}?limit=2")).json()

    assert [c["room_sid"] for c in body["calls"]] == ["RM_lim2", "RM_lim1"]


async def test_limit_outside_the_cap_is_refused(client):
    assert (await client.get(f"{_URL}?limit=0")).status_code == 422
    assert (await client.get(f"{_URL}?limit=201")).status_code == 422
    assert (await client.get(f"{_URL}?limit=200")).status_code == 200


async def test_load_test_traffic_is_excluded(client, gateway):
    """``is_probe`` rows are synthetic; the repository default keeps them out of
    numbers a reader takes for production, and this endpoint offers no knob to
    mix them back in."""
    await _answered_call(gateway.storage, room_sid="RM_real")
    await _answered_call(gateway.storage, room_sid="RM_probe", is_probe=True)

    body = (await client.get(_URL)).json()

    assert [c["room_sid"] for c in body["calls"]] == ["RM_real"]


# --- auth -------------------------------------------------------------------


async def test_auth_is_required_when_api_keys_are_configured(tmp_path, monkeypatch):
    """Router-level require_principal: a handler cannot forget to opt in."""
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "calls-auth.db"))
    config = _write_config(
        tmp_path, {"auth": {"api_keys": [{"name": "ui", "token": "sk-configured"}]}}
    )
    gw = Gateway(config_path=config)
    transport = ASGITransport(app=build_app(gw))

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get(_URL)).status_code == 401
        assert (
            await c.get(_URL, headers={"Authorization": "Bearer wrong"})
        ).status_code == 401
        ok = await c.get(_URL, headers={"Authorization": "Bearer sk-configured"})

    assert ok.status_code == 200
    assert ok.json() == {"calls": []}


async def test_a_tenant_key_never_reads_another_tenants_calls(client, gateway):
    """Calls carry the caller's phone number in ``sip.*``. A tenant-bound key
    sees its own rows and nothing else."""
    await _answered_call(gateway.storage, room_sid="RM_acme", tenant_id="acme")
    await _answered_call(gateway.storage, room_sid="RM_beta", tenant_id="beta")
    async with gateway.storage._conn.session() as db:
        created = await api_keys.create_api_key(db, name="acme-ui", tenant_id="acme")

    resp = await client.get(
        _URL, headers={"Authorization": f"Bearer {created.plaintext}"}
    )

    assert resp.status_code == 200
    assert [c["room_sid"] for c in resp.json()["calls"]] == ["RM_acme"]


async def test_the_operator_default_still_sees_every_call(client, gateway):
    """No credential = the self-hosted operator, unchanged: all rows."""
    await _answered_call(gateway.storage, room_sid="RM_acme2", tenant_id="acme")
    await _answered_call(gateway.storage, room_sid="RM_none")

    body = (await client.get(_URL)).json()

    assert {c["room_sid"] for c in body["calls"]} == {"RM_acme2", "RM_none"}


async def test_a_tenant_key_gets_a_full_page_not_a_short_one(client, gateway):
    """``limit`` means the same thing for a scoped reader as for the operator.

    The endpoint used to read ``limit`` rows and drop the other tenants' in
    Python, so this fixture (the six newest calls all belong to beta) handed acme
    an EMPTY page while six acme calls sat one row past the read boundary. With
    the predicate in the query the page is acme's own six, newest first.
    """
    for i in range(6):
        await _answered_call(
            gateway.storage,
            room_sid=f"RM_pg_acme{i}",
            tenant_id="acme",
            started_at_ms=1_750_000_000_000 + i * 1000,
        )
    for i in range(6):
        await _answered_call(
            gateway.storage,
            room_sid=f"RM_pg_beta{i}",
            tenant_id="beta",
            started_at_ms=1_750_000_100_000 + i * 1000,
        )
    async with gateway.storage._conn.session() as db:
        created = await api_keys.create_api_key(db, name="acme-page", tenant_id="acme")

    resp = await client.get(
        f"{_URL}?limit=6", headers={"Authorization": f"Bearer {created.plaintext}"}
    )

    assert resp.status_code == 200
    calls = resp.json()["calls"]
    assert [c["room_sid"] for c in calls] == [
        f"RM_pg_acme{i}" for i in reversed(range(6))
    ]
    assert {c["tenant_id"] for c in calls} == {"acme"}


async def test_a_key_with_no_tenant_reads_the_unattributed_bucket(client, gateway):
    """A non-admin key whose ``tenant_id`` is NULL resolves to ``""``, which the
    query reads as ``tenant_id IS NULL`` -- never as every tenant."""
    await _answered_call(gateway.storage, room_sid="RM_unattributed")
    await _answered_call(gateway.storage, room_sid="RM_owned", tenant_id="acme")
    async with gateway.storage._conn.session() as db:
        created = await api_keys.create_api_key(db, name="no-tenant-ui")

    resp = await client.get(
        _URL, headers={"Authorization": f"Bearer {created.plaintext}"}
    )

    assert resp.status_code == 200
    assert [c["room_sid"] for c in resp.json()["calls"]] == ["RM_unattributed"]


# --- storage disabled -------------------------------------------------------


async def test_storage_disabled_returns_503_not_an_empty_list(tmp_path, monkeypatch):
    """"No call has been recorded" and "this deployment records nothing" are
    different facts."""
    monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)
    config = _write_config(tmp_path, {"cost_tracking": {"enabled": False}})
    gw = Gateway(config_path=config)
    assert gw.storage is None
    transport = ASGITransport(app=build_app(gw, enable_dashboard=False))

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get(_URL)

    assert resp.status_code == 503
