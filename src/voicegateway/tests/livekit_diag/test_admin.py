from types import SimpleNamespace

import pytest

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
    async def list_dispatch(self, req):
        rows = [SimpleNamespace(agent_name="concierge")] if req.room == "r2" else []
        return SimpleNamespace(agent_dispatches=rows)


class _FakeApi:
    def __init__(self):
        self.room = _Rooms()
        self.agent_dispatch = _Dispatch()

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


async def test_join_token_is_a_jwt():
    admin = LiveKitAdmin(LiveKitCreds("u", "k", "s"), api=_FakeApi())
    token = admin.join_token("room1", "probe")
    assert token.count(".") == 2  # header.payload.signature
