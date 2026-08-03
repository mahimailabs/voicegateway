"""POST /v1/livekit/webhook: the signature guard, then the event -> row mapping.

The first four tests are the ones that matter: this endpoint is reachable from
the public internet and the webhook signature is its **entire** authentication
boundary, so an unsigned, tampered, wrongly-signed, or unverifiable-because-
unconfigured request must be rejected and must write nothing at all.
"""

from __future__ import annotations

import base64
import hashlib

import pytest
import yaml
from google.protobuf.json_format import MessageToJson
from httpx import ASGITransport, AsyncClient
from livekit.api import AccessToken
from livekit.protocol import models as lkm
from livekit.protocol.webhook import WebhookEvent

from voicegateway.core.gateway import Gateway
from voicegateway.server import build_app

_KEY = "APIwebhooktest"
_SECRET = "s3cret-webhook-signing-key-not-real"

_BASE_CONFIG = {
    "providers": {"openai": {"api_key": "test-key"}},
    "models": {"stt": {}, "llm": {}, "tts": {}},
    "projects": {},
    "fallbacks": {"stt": [], "llm": [], "tts": []},
    "cost_tracking": {"enabled": True},
}

_URL = "/v1/livekit/webhook"


def _write_config(tmp_path, extra: dict | None = None) -> str:
    path = tmp_path / "voicegw.yaml"
    with open(path, "w") as fh:
        yaml.dump({**_BASE_CONFIG, **(extra or {})}, fh)
    return str(path)


@pytest.fixture
def no_livekit_env(tmp_path, monkeypatch):
    """Pin credential resolution to this test.

    ``resolve_creds`` falls back to ``$VOICEGW_CONFIG``, then ``./voicegw.yaml``,
    then ``~/.config/voicegateway/voicegw.yaml``, so without pinning the config
    path a developer machine that happens to have a ``livekit:`` block would
    quietly supply real credentials to a test asserting there are none.
    """
    monkeypatch.delenv("LIVEKIT_API_KEY", raising=False)
    monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)
    monkeypatch.delenv("VOICEGW_LOADTEST_TRUNK_IDS", raising=False)
    monkeypatch.setenv("VOICEGW_CONFIG", _write_config(tmp_path))


@pytest.fixture
def gateway(tmp_path, monkeypatch, no_livekit_env):
    """A gateway whose LiveKit webhook creds come from the environment."""
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "webhook.db"))
    monkeypatch.setenv("LIVEKIT_API_KEY", _KEY)
    monkeypatch.setenv("LIVEKIT_API_SECRET", _SECRET)
    return Gateway(config_path=_write_config(tmp_path))


@pytest.fixture
def unconfigured_gateway(tmp_path, monkeypatch, no_livekit_env):
    """A gateway with NO LiveKit credentials anywhere: env or config."""
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "webhook-noauth.db"))
    return Gateway(config_path=_write_config(tmp_path))


def _client(gw: Gateway) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=build_app(gw)), base_url="http://t")


def _sign(body: bytes, key: str = _KEY, secret: str = _SECRET) -> str:
    """The header LiveKit sends: a JWT whose sha256 claim covers exactly ``body``."""
    digest = base64.b64encode(hashlib.sha256(body).digest()).decode()
    return AccessToken(key, secret).with_sha256(digest).to_jwt()


def _body(event: WebhookEvent) -> bytes:
    return MessageToJson(event).encode()


async def _post(gw: Gateway, body: bytes, token: str | None):
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = token
    async with _client(gw) as client:
        return await client.post(_URL, content=body, headers=headers)


async def _send(gw: Gateway, event: WebhookEvent):
    body = _body(event)
    return await _post(gw, body, _sign(body))


def _room(
    sid: str = "RM_test", name: str = "call-1", created_ms: int = 1_800_000_000_000
):
    return lkm.Room(sid=sid, name=name, creation_time_ms=created_ms)


def _sip_participant(
    sid: str = "PA_caller",
    *,
    reason: int = 0,
    trunk: str = "ST_prod",
    joined_ms: int = 1_800_000_000_100,
):
    return lkm.ParticipantInfo(
        sid=sid,
        identity="sip_+15195550100",
        kind=lkm.ParticipantInfo.Kind.SIP,
        region="us-east",
        joined_at_ms=joined_ms,
        disconnect_reason=reason,
        attributes={
            "sip.callID": "call-abc",
            "sip.trunkID": trunk,
            "sip.phoneNumber": "+15195550100",
            "lk.agent.state": "ignored-non-sip-attribute",
        },
    )


async def _calls(gw: Gateway) -> list[dict]:
    return await gw.storage.list_calls(limit=50, is_probe=None)


# --- the security boundary --------------------------------------------------


async def test_request_with_no_signature_is_rejected_and_writes_nothing(gateway):
    """No Authorization header at all: the public internet gets nothing."""
    resp = await _post(
        gateway, _body(WebhookEvent(event="room_started", room=_room())), None
    )

    assert resp.status_code == 401
    assert await _calls(gateway) == []


async def test_missing_credentials_fails_closed(unconfigured_gateway):
    """No LIVEKIT_API_KEY/SECRET configured: reject, never "no creds so skip".

    The token here is perfectly well-formed and correctly signs the body. The
    only reason it cannot be verified is that the deployment has no credentials,
    which is exactly the case a bypass branch would wave through.
    """
    body = _body(WebhookEvent(event="room_started", room=_room()))
    resp = await _post(unconfigured_gateway, body, _sign(body))

    assert resp.status_code == 503
    assert await _calls(unconfigured_gateway) == []


async def test_body_tampered_after_signing_is_rejected(gateway):
    """A valid token over a *different* body must not authenticate this one."""
    signed = _body(WebhookEvent(event="room_started", room=_room(sid="RM_signed")))
    tampered = _body(WebhookEvent(event="room_started", room=_room(sid="RM_injected")))

    resp = await _post(gateway, tampered, _sign(signed))

    assert resp.status_code == 401
    assert await _calls(gateway) == []


async def test_token_signed_with_another_secret_is_rejected(gateway):
    body = _body(WebhookEvent(event="room_started", room=_room()))
    resp = await _post(gateway, body, _sign(body, secret="attacker-secret"))

    assert resp.status_code == 401
    assert await _calls(gateway) == []


async def test_token_from_another_api_key_is_rejected(gateway):
    """Wrong issuer: a token minted for a different LiveKit project."""
    body = _body(WebhookEvent(event="room_started", room=_room()))
    resp = await _post(gateway, body, _sign(body, key="APIsomeoneelse"))

    assert resp.status_code == 401
    assert await _calls(gateway) == []


async def test_unparseable_body_is_rejected_as_unverified_not_as_malformed(gateway):
    """Verify-before-parse: garbage with no signature is a 401, not a 400.

    A 400 here would mean the parser ran on an unverified body.
    """
    resp = await _post(gateway, b"} not json at all {", None)

    assert resp.status_code == 401


async def test_unparseable_body_with_a_valid_signature_is_a_400(gateway):
    """The mirror image: only an authenticated sender can reach the parser."""
    body = b'{"event": [broken'
    resp = await _post(gateway, body, _sign(body))

    assert resp.status_code == 400
    assert await _calls(gateway) == []


async def test_rejection_does_not_echo_the_body_or_the_reason(gateway):
    """No verification oracle: one status, one terse message, no detail."""
    body = _body(WebhookEvent(event="room_started", room=_room(sid="RM_secret")))
    resp = await _post(gateway, body, _sign(body, secret="wrong"))

    assert "RM_secret" not in resp.text
    assert "signature" not in resp.text.lower()
    assert "hash" not in resp.text.lower()


async def test_credentials_from_the_config_file_verify(
    tmp_path, monkeypatch, no_livekit_env
):
    """An operator who configured LiveKit in voicegw.yaml, not the env.

    ``VOICEGW_CONFIG`` is how the daemon publishes the config it was served
    with (``serve_cli`` sets it), and it is where ``resolve_creds`` looks.
    """
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "webhook-yaml.db"))
    config = _write_config(
        tmp_path,
        {"livekit": {"url": "wss://x", "api_key": _KEY, "api_secret": _SECRET}},
    )
    monkeypatch.setenv("VOICEGW_CONFIG", config)
    gw = Gateway(config_path=config)

    resp = await _send(gw, WebhookEvent(event="room_started", room=_room()))

    assert resp.status_code == 200
    assert len(await _calls(gw)) == 1


# --- event -> row mapping ---------------------------------------------------


async def test_room_started_creates_the_call(gateway):
    resp = await _send(
        gateway,
        WebhookEvent(event="room_started", room=_room(), created_at=1_800_000_000),
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    rows = await _calls(gateway)
    assert len(rows) == 1
    assert rows[0]["room_sid"] == "RM_test"
    assert rows[0]["room_name"] == "call-1"
    assert rows[0]["origin"] == "webhook"
    assert rows[0]["started_at_ms"] == 1_800_000_000_000
    assert rows[0]["ended_at_ms"] is None
    assert rows[0]["num_legs"] == 0


async def test_room_finished_closes_the_call_and_derives_duration(gateway):
    await _send(gateway, WebhookEvent(event="room_started", room=_room()))
    await _send(
        gateway,
        WebhookEvent(event="room_finished", room=_room(), created_at=1_800_000_042),
    )

    rows = await _calls(gateway)
    assert len(rows) == 1
    assert rows[0]["ended_at_ms"] == 1_800_000_042_000
    assert rows[0]["duration_ms"] == 42_000


async def test_participant_joined_writes_the_leg_with_sip_attributes_only(gateway):
    await _send(
        gateway,
        WebhookEvent(
            event="participant_joined",
            room=_room(),
            participant=_sip_participant(),
            created_at=1_800_000_001,
        ),
    )

    rows = await _calls(gateway)
    assert len(rows) == 1
    assert rows[0]["channel"] == "sip"
    assert rows[0]["num_legs"] == 1

    legs = await gateway.storage.list_call_legs(rows[0]["id"])
    assert len(legs) == 1
    assert legs[0]["participant_sid"] == "PA_caller"
    assert legs[0]["identity"] == "sip_+15195550100"
    assert legs[0]["kind"] == "SIP"
    assert legs[0]["region"] == "us-east"
    assert legs[0]["joined_at_ms"] == 1_800_000_000_100
    assert legs[0]["left_at_ms"] is None
    assert legs[0]["disconnect_reason"] is None
    # sip.* keys only: the agent-state attribute is not copied.
    import json

    assert json.loads(legs[0]["attributes_json"]) == {
        "sip.callID": "call-abc",
        "sip.phoneNumber": "+15195550100",
        "sip.trunkID": "ST_prod",
    }


async def test_sip_trunk_failure_is_readable_end_to_end(gateway):
    """The M1 demoable: a call that ran zero inference, and why it failed."""
    await _send(
        gateway,
        WebhookEvent(
            event="participant_connection_aborted",
            room=_room(sid="RM_fail"),
            participant=_sip_participant(
                sid="PA_fail", reason=lkm.DisconnectReason.SIP_TRUNK_FAILURE
            ),
            created_at=1_800_000_005,
        ),
    )

    rows = await _calls(gateway)
    assert len(rows) == 1
    assert rows[0]["end_reason"] == "SIP_TRUNK_FAILURE"

    legs = await gateway.storage.list_call_legs(rows[0]["id"])
    assert legs[0]["disconnect_reason"] == "SIP_TRUNK_FAILURE"
    assert legs[0]["left_at_ms"] == 1_800_000_005_000


async def test_unknown_disconnect_reason_is_not_recorded(gateway):
    """UNKNOWN_REASON is proto's unset 0; recording it would fake an observation."""
    await _send(
        gateway,
        WebhookEvent(
            event="participant_left",
            room=_room(),
            participant=_sip_participant(reason=lkm.DisconnectReason.UNKNOWN_REASON),
            created_at=1_800_000_009,
        ),
    )

    rows = await _calls(gateway)
    assert rows[0]["end_reason"] is None
    legs = await gateway.storage.list_call_legs(rows[0]["id"])
    assert legs[0]["disconnect_reason"] is None
    assert legs[0]["left_at_ms"] == 1_800_000_009_000


async def test_an_agent_leg_does_not_set_the_calls_end_reason(gateway):
    """The call's end reason is the caller's, not the agent's."""
    await _send(
        gateway,
        WebhookEvent(
            event="participant_left",
            room=_room(),
            participant=lkm.ParticipantInfo(
                sid="PA_agent",
                identity="agent-1",
                kind=lkm.ParticipantInfo.Kind.AGENT,
                disconnect_reason=lkm.DisconnectReason.CLIENT_INITIATED,
            ),
            created_at=1_800_000_010,
        ),
    )

    rows = await _calls(gateway)
    assert rows[0]["end_reason"] is None
    assert rows[0]["channel"] is None
    legs = await gateway.storage.list_call_legs(rows[0]["id"])
    assert legs[0]["kind"] == "AGENT"
    assert legs[0]["disconnect_reason"] == "CLIENT_INITIATED"


async def test_audio_track_published_records_the_first_audio_track(gateway):
    await _send(
        gateway,
        WebhookEvent(
            event="track_published",
            room=_room(),
            participant=lkm.ParticipantInfo(
                sid="PA_agent", identity="agent-1", kind=lkm.ParticipantInfo.Kind.AGENT
            ),
            track=lkm.TrackInfo(
                sid="TR_1", type=lkm.TrackType.AUDIO, mime_type="audio/opus"
            ),
            created_at=1_800_000_003,
        ),
    )

    rows = await _calls(gateway)
    legs = await gateway.storage.list_call_legs(rows[0]["id"])
    assert legs[0]["first_audio_track_at_ms"] == 1_800_000_003_000
    assert legs[0]["audio_track_sid"] == "TR_1"
    assert legs[0]["audio_codec"] == "audio/opus"


async def test_video_track_published_leaves_the_audio_columns_empty(gateway):
    await _send(
        gateway,
        WebhookEvent(
            event="track_published",
            room=_room(),
            participant=lkm.ParticipantInfo(sid="PA_web", identity="web-1"),
            track=lkm.TrackInfo(
                sid="TR_v", type=lkm.TrackType.VIDEO, mime_type="video/VP8"
            ),
            created_at=1_800_000_004,
        ),
    )

    rows = await _calls(gateway)
    legs = await gateway.storage.list_call_legs(rows[0]["id"])
    assert legs[0]["first_audio_track_at_ms"] is None
    assert legs[0]["audio_track_sid"] is None
    assert legs[0]["audio_codec"] is None


async def test_track_published_with_no_track_reported_claims_no_audio(gateway):
    """An unset track field reads as TrackType.AUDIO (enum 0). Do not believe it."""
    await _send(
        gateway,
        WebhookEvent(
            event="track_published",
            room=_room(),
            participant=lkm.ParticipantInfo(sid="PA_agent", identity="agent-1"),
            created_at=1_800_000_005,
        ),
    )

    rows = await _calls(gateway)
    legs = await gateway.storage.list_call_legs(rows[0]["id"])
    assert legs[0]["first_audio_track_at_ms"] is None
    assert legs[0]["audio_track_sid"] is None


async def test_only_the_earliest_audio_track_is_kept(gateway):
    for track_sid, at in (("TR_1", 1_800_000_006), ("TR_2", 1_800_000_007)):
        await _send(
            gateway,
            WebhookEvent(
                event="track_published",
                room=_room(),
                participant=lkm.ParticipantInfo(sid="PA_agent", identity="agent-1"),
                track=lkm.TrackInfo(sid=track_sid, mime_type="audio/opus"),
                created_at=at,
            ),
        )

    rows = await _calls(gateway)
    legs = await gateway.storage.list_call_legs(rows[0]["id"])
    assert legs[0]["first_audio_track_at_ms"] == 1_800_000_006_000


async def test_out_of_order_delivery_converges_on_one_call(gateway):
    """participant_left may legitimately be the first event we ever see."""
    await _send(
        gateway,
        WebhookEvent(
            event="participant_left",
            room=lkm.Room(sid="RM_ooo", name="late"),
            participant=_sip_participant(
                sid="PA_1", reason=lkm.DisconnectReason.USER_REJECTED
            ),
            created_at=1_800_000_020,
        ),
    )
    await _send(
        gateway,
        WebhookEvent(
            event="room_started",
            room=_room(sid="RM_ooo", name="late", created_ms=1_800_000_015_000),
            created_at=1_800_000_015,
        ),
    )

    rows = await _calls(gateway)
    assert len(rows) == 1
    assert rows[0]["started_at_ms"] == 1_800_000_015_000
    assert rows[0]["end_reason"] == "USER_REJECTED"
    assert rows[0]["num_legs"] == 1


async def test_redelivery_does_not_duplicate_the_leg_or_inflate_num_legs(gateway):
    event = WebhookEvent(
        event="participant_joined",
        room=_room(),
        participant=_sip_participant(),
        created_at=1_800_000_001,
    )
    for _ in range(3):
        assert (await _send(gateway, event)).status_code == 200

    rows = await _calls(gateway)
    assert len(rows) == 1
    assert rows[0]["num_legs"] == 1
    assert len(await gateway.storage.list_call_legs(rows[0]["id"])) == 1


async def test_an_event_with_no_room_sid_is_accepted_but_not_stored(gateway):
    """Nothing to key on. Retrying will not help, so do not ask LiveKit to."""
    resp = await _send(
        gateway,
        WebhookEvent(event="room_started", room=lkm.Room(name="no-sid")),
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ignored"}
    assert await _calls(gateway) == []


async def test_egress_events_are_accepted_without_writing(gateway):
    """No column in the M1 schema describes an egress. Accept, do not invent."""
    resp = await _send(gateway, WebhookEvent(event="egress_started", room=_room()))

    assert resp.status_code == 200
    assert resp.json() == {"status": "ignored"}
    assert await _calls(gateway) == []


async def test_track_unpublished_keeps_the_call_but_writes_no_leg_columns(gateway):
    resp = await _send(
        gateway,
        WebhookEvent(
            event="track_unpublished",
            room=_room(),
            participant=lkm.ParticipantInfo(sid="PA_agent", identity="agent-1"),
            track=lkm.TrackInfo(sid="TR_1", mime_type="audio/opus"),
            created_at=1_800_000_008,
        ),
    )

    assert resp.status_code == 200
    rows = await _calls(gateway)
    legs = await gateway.storage.list_call_legs(rows[0]["id"])
    assert legs[0]["first_audio_track_at_ms"] is None
    assert legs[0]["left_at_ms"] is None


# --- is_probe comes from the trunk, never from the wire ---------------------


async def test_is_probe_is_set_from_a_configured_loadtest_trunk(gateway, monkeypatch):
    monkeypatch.setenv("VOICEGW_LOADTEST_TRUNK_IDS", "ST_load,ST_load_eu")

    await _send(
        gateway,
        WebhookEvent(
            event="participant_joined",
            room=_room(sid="RM_load"),
            participant=_sip_participant(trunk="ST_load"),
            created_at=1_800_000_030,
        ),
    )

    rows = await gateway.storage.list_calls(limit=10, is_probe=True)
    assert len(rows) == 1
    assert rows[0]["room_sid"] == "RM_load"
    # And it is excluded from the production default view.
    assert await gateway.storage.list_calls(limit=10) == []


async def test_production_trunk_is_not_flagged_as_a_probe(gateway, monkeypatch):
    monkeypatch.setenv("VOICEGW_LOADTEST_TRUNK_IDS", "ST_load")

    await _send(
        gateway,
        WebhookEvent(
            event="participant_joined",
            room=_room(sid="RM_prod"),
            participant=_sip_participant(trunk="ST_prod"),
            created_at=1_800_000_031,
        ),
    )

    rows = await gateway.storage.list_calls(limit=10)
    assert [r["room_sid"] for r in rows] == ["RM_prod"]
    assert rows[0]["is_probe"] == 0


async def test_with_no_trunk_configured_nothing_is_flagged(gateway):
    await _send(
        gateway,
        WebhookEvent(
            event="participant_joined",
            room=_room(sid="RM_unconf"),
            participant=_sip_participant(trunk="ST_load"),
            created_at=1_800_000_032,
        ),
    )

    assert await gateway.storage.list_calls(limit=10, is_probe=True) == []
    assert len(await gateway.storage.list_calls(limit=10)) == 1


async def test_a_later_event_never_clears_the_probe_flag(gateway, monkeypatch):
    """room_finished cannot see a trunk id, so it must not un-flag the call."""
    monkeypatch.setenv("VOICEGW_LOADTEST_TRUNK_IDS", "ST_load")

    await _send(
        gateway,
        WebhookEvent(
            event="participant_joined",
            room=_room(sid="RM_load2"),
            participant=_sip_participant(trunk="ST_load"),
            created_at=1_800_000_033,
        ),
    )
    await _send(
        gateway,
        WebhookEvent(
            event="room_finished", room=_room(sid="RM_load2"), created_at=1_800_000_034
        ),
    )

    rows = await gateway.storage.list_calls(limit=10, is_probe=True)
    assert len(rows) == 1
    assert rows[0]["ended_at_ms"] == 1_800_000_034_000
