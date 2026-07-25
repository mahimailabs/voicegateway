import dataclasses
from types import SimpleNamespace

from livekit.protocol import egress as egpb
from livekit.protocol import ingress as inpb
from livekit.protocol import sip as sippb

from voicegateway.livekit_diag.admin import LiveKitAdmin
from voicegateway.livekit_diag.config import LiveKitCreds

AGENT = 4  # ParticipantInfo.Kind.AGENT value in the proto
STD = 0


class _Rooms:
    async def list_rooms(self, _req):
        return SimpleNamespace(rooms=[SimpleNamespace(name="r1"), SimpleNamespace(name="r2")])

    async def list_participants(self, req):
        table = {
            "r1": [
                SimpleNamespace(identity="agent-x", name="realty", kind=AGENT, joined_at=100),
                SimpleNamespace(identity="human-1", name="", kind=STD, joined_at=100),
            ],
            "r2": [SimpleNamespace(identity="human-2", name="", kind=STD, joined_at=100)],
        }
        return SimpleNamespace(participants=table[req.room])


class _Dispatch:
    async def list_dispatch(self, room_name):
        # Mirrors the real SDK: takes the room NAME (str), returns a list directly.
        return [SimpleNamespace(agent_name="concierge")] if room_name == "r2" else []


class _Egress:
    async def list_egress(self, _req):
        return SimpleNamespace(
            items=[
                egpb.EgressInfo(
                    egress_id="eg1",
                    status=egpb.EgressStatus.EGRESS_ACTIVE,
                    source_type=egpb.EgressSourceType.EGRESS_SOURCE_TYPE_WEB,
                    room_name="r1",
                    started_at=123,
                    # A nested secret (the RTMP push URL embeds the stream key):
                    # the row must never carry it.
                    stream_results=[
                        egpb.StreamInfo(url="rtmp://secret.example/live/SECRET-KEY")
                    ],
                )
            ]
        )


class _Ingress:
    async def list_ingress(self, _req):
        return SimpleNamespace(
            items=[
                inpb.IngressInfo(
                    ingress_id="in1",
                    name="stream",
                    input_type=inpb.IngressInput.RTMP_INPUT,
                    room_name="r2",
                    # Secrets the row must NOT surface:
                    stream_key="SECRET-STREAM-KEY",
                    url="rtmp://secret.example/live",
                    state=inpb.IngressState(
                        status=inpb.IngressState.Status.ENDPOINT_PUBLISHING
                    ),
                )
            ]
        )


class _Sip:
    async def list_sip_inbound_trunk(self, _req):
        return SimpleNamespace(
            items=[
                sippb.SIPInboundTrunkInfo(
                    sip_trunk_id="tin1",
                    name="main-in",
                    numbers=["+15551234567"],
                    auth_username="SECRET-USER",
                    auth_password="SECRET-PASS",
                )
            ]
        )

    async def list_sip_outbound_trunk(self, _req):
        return SimpleNamespace(
            items=[
                sippb.SIPOutboundTrunkInfo(
                    sip_trunk_id="tout1",
                    name="main-out",
                    address="sip.example.com",
                    transport=sippb.SIPTransport.SIP_TRANSPORT_TCP,
                    numbers=["+15559876543"],
                    auth_password="SECRET-PASS",
                )
            ]
        )

    async def list_sip_dispatch_rule(self, _req):
        return SimpleNamespace(
            items=[
                sippb.SIPDispatchRuleInfo(
                    sip_dispatch_rule_id="dr1", name="rule", trunk_ids=["tin1"]
                )
            ]
        )


class _FakeApi:
    def __init__(self):
        self.room = _Rooms()
        self.agent_dispatch = _Dispatch()
        self.egress = _Egress()
        self.ingress = _Ingress()
        self.sip = _Sip()

    async def aclose(self):
        pass


async def test_list_agents_joins_participants_and_dispatch():
    admin = LiveKitAdmin(LiveKitCreds("u", "k", "s"), api=_FakeApi())
    rows = await admin.list_agents()
    by_name = {r.agent_name: r for r in rows}
    assert set(by_name) == {"realty", "concierge"}
    assert by_name["realty"].room == "r1"
    assert by_name["realty"].state == "active"
    assert by_name["realty"].humans == 1
    assert by_name["concierge"].state == "dispatched"


async def test_room_participant_identities_lists_everyone_in_the_room():
    """The probe reads this to confirm a dispatched worker actually joined."""
    admin = LiveKitAdmin(LiveKitCreds("u", "k", "s"), api=_FakeApi())
    idents = await admin.room_participant_identities("r1")
    assert idents == ["agent-x", "human-1"]


async def test_join_token_is_a_jwt():
    admin = LiveKitAdmin(LiveKitCreds("u", "k", "s"), api=_FakeApi())
    token = admin.join_token("room1", "probe")
    assert token.count(".") == 2  # header.payload.signature


async def test_list_egress_maps_status_and_source_enums():
    admin = LiveKitAdmin(LiveKitCreds("u", "k", "s"), api=_FakeApi())
    rows = await admin.list_egress()
    assert len(rows) == 1
    assert rows[0].egress_id == "eg1"
    assert rows[0].status == "EGRESS_ACTIVE"
    assert rows[0].source_type == "EGRESS_SOURCE_TYPE_WEB"
    assert rows[0].room_name == "r1"
    assert rows[0].started_at == 123
    # The nested stream URL (a secret) never reaches the row.
    keys = dataclasses.asdict(rows[0]).keys()
    assert "url" not in keys and "stream_results" not in keys


async def test_list_egress_unknown_enum_falls_back_to_int():
    """A server enum value the local descriptor does not know renders as its int."""

    class _EgUnknown:
        async def list_egress(self, _req):
            return SimpleNamespace(
                items=[
                    egpb.EgressInfo(
                        egress_id="e9",
                        status=999,  # not a known EgressStatus value
                        source_type=egpb.EgressSourceType.EGRESS_SOURCE_TYPE_WEB,
                        room_name="r",
                        started_at=0,
                    )
                ]
            )

    api = _FakeApi()
    api.egress = _EgUnknown()
    admin = LiveKitAdmin(LiveKitCreds("u", "k", "s"), api=api)
    rows = await admin.list_egress()
    assert rows[0].status == "999"


async def test_list_ingress_without_state_yields_blank_status():
    """An ingress with no state message reports an empty status, not a crash."""

    class _InNoState:
        async def list_ingress(self, _req):
            return SimpleNamespace(
                items=[
                    inpb.IngressInfo(
                        ingress_id="in9",
                        name="x",
                        input_type=inpb.IngressInput.WHIP_INPUT,
                        room_name="r",
                    )
                ]
            )

    api = _FakeApi()
    api.ingress = _InNoState()
    admin = LiveKitAdmin(LiveKitCreds("u", "k", "s"), api=api)
    rows = await admin.list_ingress()
    assert rows[0].status == ""
    assert rows[0].input_type == "WHIP_INPUT"


async def test_list_ingress_maps_enums_and_excludes_secrets():
    admin = LiveKitAdmin(LiveKitCreds("u", "k", "s"), api=_FakeApi())
    rows = await admin.list_ingress()
    assert rows[0].ingress_id == "in1"
    assert rows[0].input_type == "RTMP_INPUT"
    assert rows[0].status == "ENDPOINT_PUBLISHING"
    # The stream key and url are secrets and must never reach the row.
    keys = dataclasses.asdict(rows[0]).keys()
    assert "stream_key" not in keys and "url" not in keys


async def test_list_sip_excludes_auth_secrets():
    admin = LiveKitAdmin(LiveKitCreds("u", "k", "s"), api=_FakeApi())
    inbound = await admin.list_sip_inbound_trunks()
    outbound = await admin.list_sip_outbound_trunks()
    rules = await admin.list_sip_dispatch_rules()

    assert inbound[0].trunk_id == "tin1"
    assert inbound[0].numbers == ["+15551234567"]
    assert outbound[0].address == "sip.example.com"
    assert outbound[0].transport == "SIP_TRANSPORT_TCP"
    assert rules[0].rule_id == "dr1"
    assert rules[0].trunk_ids == ["tin1"]

    # No auth credential fields leak onto any trunk row.
    for row in (inbound[0], outbound[0]):
        keys = dataclasses.asdict(row).keys()
        assert "auth_username" not in keys
        assert "auth_password" not in keys
