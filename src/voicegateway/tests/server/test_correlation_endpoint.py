"""GET /api/correlation: the read side of the sessions <-> calls join rate.

Three things these tests exist to hold:

1. **The payload shape is the contract.** The dashboard's correlation card
   (``src/dashboard/frontend/src/lib/types.ts``) is written against
   ``CorrelationRate`` field for field, and a renamed or dropped field renders
   as a permanently blank card that still compiles. The field list below is
   copied from that file and frozen, so the mismatch fails here.
2. **The endpoint computes nothing and flattens nothing.** The rate, the counts,
   the threshold and the status are all decided in
   ``session_repository.read_correlation_rate``; what it returns is what is
   served. An empty denominator comes back as ``rate: null`` with
   ``status: "unknown"``, NEVER as ``0.0``: "no session in this deployment ever
   had a room to join" is not "nothing correlates", and the second reads as an
   outage.
3. **The warn threshold travels with the number.** A verdict whose threshold is
   invisible cannot be checked by the reader, so ``warn_threshold`` is part of
   the payload and matches the repository constant.
"""

from __future__ import annotations

import time
import uuid

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.models.request_model import RequestRecord
from voicegateway.repository import api_keys_repository as api_keys
from voicegateway.repository import session_repository as sessions
from voicegateway.server import build_app

_URL = "/api/correlation"
_PROJECT = "tony-pizza"

# Copied from `CorrelationRate` in src/dashboard/frontend/src/lib/types.ts, in
# the order the dataclass declares them. The UI is already written against
# exactly these names.
_FIELDS = [
    "eligible",
    "correlated",
    "rate",
    "ambiguous",
    "dangling",
    "no_room",
    "warn_threshold",
    "status",
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
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "correlation-endpoint.db"))
    return Gateway(config_path=_write_config(tmp_path))


@pytest.fixture
async def client(gateway):
    transport = ASGITransport(app=build_app(gateway))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _record(*, session_id: str, room: str | None) -> RequestRecord:
    metadata: dict[str, object] = {}
    if room is not None:
        metadata["room"] = room
    return RequestRecord(
        id=str(uuid.uuid4()),
        timestamp=time.time(),
        modality="llm",
        model_id="openai/llm-test",
        provider="openai",
        project=_PROJECT,
        cost_usd=0.001,
        session_id=session_id,
        metadata=metadata,
    )


async def _session_in_room(storage, *, session_id: str, room: str | None) -> None:
    """One session, with the room it ran in (or none, for web/Pipecat)."""
    await storage.log_request(_record(session_id=session_id, room=room))


async def _call_in_room(storage, *, room_sid: str, room_name: str) -> str:
    """One webhook-originated call row, as the receiver would write it."""
    return await storage.upsert_call(
        origin="webhook",
        room_sid=room_sid,
        room_name=room_name,
        project=_PROJECT,
        started_at_ms=1_800_000_000_000,
    )


# --- shape ------------------------------------------------------------------


async def test_payload_fields_match_the_frontend_types_exactly(client, gateway):
    """Field for field against types.ts: no invented, renamed or dropped key."""
    await _call_in_room(gateway.storage, room_sid="RM_shape", room_name="room-shape")
    await _session_in_room(gateway.storage, session_id="vg-shape", room="room-shape")

    resp = await client.get(_URL)

    assert resp.status_code == 200
    assert list(resp.json()) == _FIELDS


# --- the endpoint computes nothing, and flattens nothing ---------------------


async def test_the_served_number_is_the_one_the_repository_computed(client, gateway):
    """Pure passthrough: no rounding, no recomputation, no second opinion."""
    await _call_in_room(gateway.storage, room_sid="RM_ok", room_name="room-ok")
    await _session_in_room(gateway.storage, session_id="vg-joined", room="room-ok")
    await _session_in_room(gateway.storage, session_id="vg-orphan", room="room-nocall")
    await _session_in_room(gateway.storage, session_id="vg-web", room=None)

    body = (await client.get(_URL)).json()
    computed = await gateway.storage.read_correlation_rate()

    assert body == computed
    assert body["eligible"] == 2
    assert body["correlated"] == 1
    assert body["rate"] == pytest.approx(0.5)
    assert body["no_room"] == 1


async def test_nothing_measured_is_served_as_null_never_as_zero(client):
    """The honesty case. A deployment that has recorded nothing has an empty
    denominator, and 0.0 there would report an unmeasured deployment as a
    totally broken one."""
    body = (await client.get(_URL)).json()

    assert body["eligible"] == 0
    assert body["rate"] is None
    assert body["rate"] != 0
    assert body["status"] == "unknown"


async def test_sessions_that_could_never_join_do_not_make_the_rate_zero(
    client, gateway
):
    """Web and Pipecat sessions have no room. They are reported as ``no_room``
    and left out of both sides, so they cannot fake a failure."""
    await _session_in_room(gateway.storage, session_id="vg-web-1", room=None)
    await _session_in_room(gateway.storage, session_id="vg-web-2", room=None)

    body = (await client.get(_URL)).json()

    assert body["no_room"] == 2
    assert body["eligible"] == 0
    assert body["rate"] is None
    assert body["status"] == "unknown"


async def test_a_measured_zero_is_distinct_from_nothing_measured(client, gateway):
    """A session that had a room and did not join IS a measured 0%, and must not
    be confused with the unknown case above."""
    await _session_in_room(gateway.storage, session_id="vg-orphan", room="room-nocall")

    body = (await client.get(_URL)).json()

    assert body["eligible"] == 1
    assert body["rate"] == pytest.approx(0.0)
    assert body["status"] == "warn"


async def test_the_failure_modes_are_broken_out_not_summed(client, gateway):
    """``ambiguous`` needs a different fix than a missing webhook receiver, so
    the endpoint forwards the breakdown rather than one uncorrelated total."""
    sessions._warn_ambiguous_room.cache_clear()
    await _call_in_room(gateway.storage, room_sid="RM_a", room_name="room-pinned")
    await _call_in_room(gateway.storage, room_sid="RM_b", room_name="room-pinned")
    await _session_in_room(gateway.storage, session_id="vg-amb", room="room-pinned")
    await _session_in_room(gateway.storage, session_id="vg-missing", room="room-nocall")

    body = (await client.get(_URL)).json()

    assert body["eligible"] == 2
    assert body["correlated"] == 0
    assert body["ambiguous"] == 1
    assert body["dangling"] == 0


# --- the warn threshold is published, not just applied ----------------------


async def test_the_default_threshold_is_published_with_the_number(client):
    body = (await client.get(_URL)).json()

    assert body["warn_threshold"] == pytest.approx(sessions.CORRELATION_WARN_THRESHOLD)
    assert body["status"] in sessions.CORRELATION_STATUSES


async def test_a_rate_below_the_threshold_reports_warn(client, gateway):
    """Two of three sessions joined: 66%, under the 90% default."""
    await _call_in_room(gateway.storage, room_sid="RM_w", room_name="room-w")
    await _session_in_room(gateway.storage, session_id="vg-w1", room="room-w")
    await _session_in_room(gateway.storage, session_id="vg-w2", room="room-w")
    await _session_in_room(gateway.storage, session_id="vg-w3", room="room-none")

    body = (await client.get(_URL)).json()

    assert body["rate"] == pytest.approx(2 / 3)
    assert body["rate"] < body["warn_threshold"]
    assert body["status"] == "warn"


async def test_a_healthy_deployment_reports_ok(client, gateway):
    await _call_in_room(gateway.storage, room_sid="RM_h", room_name="room-h")
    await _session_in_room(gateway.storage, session_id="vg-h1", room="room-h")
    await _session_in_room(gateway.storage, session_id="vg-h2", room="room-h")

    body = (await client.get(_URL)).json()

    assert body["rate"] == pytest.approx(1.0)
    assert body["status"] == "ok"


# --- auth -------------------------------------------------------------------


async def test_auth_is_required_when_api_keys_are_configured(tmp_path, monkeypatch):
    """Router-level require_principal: a handler cannot forget to opt in."""
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "correlation-auth.db"))
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
    assert ok.json()["status"] == "unknown"


async def test_the_operator_default_with_no_keys_still_reads_it(client, gateway):
    """No credential = the self-hosted operator, unchanged."""
    await _call_in_room(gateway.storage, room_sid="RM_op", room_name="room-op")
    await _session_in_room(gateway.storage, session_id="vg-op", room="room-op")

    resp = await client.get(_URL)

    assert resp.status_code == 200
    assert resp.json()["correlated"] == 1


async def test_a_tenant_key_is_refused_rather_than_shown_the_whole_deployment(
    client, gateway
):
    """There is no tenant dimension to this number, so a tenant-bound key would
    otherwise be handed every other tenant's session volume."""
    await _session_in_room(gateway.storage, session_id="vg-t", room="room-t")
    async with gateway.storage._conn.session() as db:
        created = await api_keys.create_api_key(db, name="acme-ui", tenant_id="acme")

    resp = await client.get(
        _URL, headers={"Authorization": f"Bearer {created.plaintext}"}
    )

    assert resp.status_code == 403


# --- storage disabled -------------------------------------------------------


async def test_storage_disabled_returns_503_not_an_unknown_rate(tmp_path, monkeypatch):
    """"Nothing has correlated yet" and "this deployment records nothing" are
    different facts."""
    monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)
    config = _write_config(tmp_path, {"cost_tracking": {"enabled": False}})
    gw = Gateway(config_path=config)
    assert gw.storage is None
    transport = ASGITransport(app=build_app(gw, enable_dashboard=False))

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get(_URL)

    assert resp.status_code == 503
