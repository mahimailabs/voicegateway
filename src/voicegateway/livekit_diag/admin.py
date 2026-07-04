"""The only unit that talks to the LiveKit server API. Owns credentials.

Wraps livekit.api for the read paths (rooms, participants, dispatches) the
diagnostics need, plus create/delete room + dispatch for the latency probe, and
join-token minting for the synthetic client.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from livekit import api
from livekit.protocol.models import ParticipantInfo

from voicegateway.livekit_diag.config import LiveKitCreds

_AGENT_KIND = ParticipantInfo.Kind.AGENT


@dataclass(frozen=True)
class AgentRow:
    agent_name: str
    room: str
    identity: str | None
    state: str  # "active" (joined) or "dispatched" (assigned, not joined)
    humans: int
    age_s: float | None


class LiveKitAdmin:
    def __init__(self, creds: LiveKitCreds, *, api: Any | None = None) -> None:
        self._creds = creds
        self._api = api or api_client(creds)

    async def list_agents(self) -> list[AgentRow]:
        now = time.time()
        rooms = (await self._api.room.list_rooms(_req("ListRoomsRequest"))).rooms
        seen: dict[tuple[str, str], AgentRow] = {}
        for room in rooms:
            parts = (
                await self._api.room.list_participants(
                    _req("ListParticipantsRequest", room=room.name)
                )
            ).participants
            humans = sum(1 for p in parts if p.kind != _AGENT_KIND)
            for p in parts:
                if p.kind == _AGENT_KIND:
                    key = (p.name or p.identity, room.name)
                    joined = getattr(p, "joined_at", 0) or 0
                    seen[key] = AgentRow(
                        agent_name=p.name or p.identity,
                        room=room.name,
                        identity=p.identity,
                        state="active",
                        humans=humans,
                        age_s=(now - joined) if joined else None,
                    )
            dispatches = (
                await self._api.agent_dispatch.list_dispatch(
                    _req("ListAgentDispatchRequest", room=room.name)
                )
            ).agent_dispatches
            for d in dispatches:
                key = (d.agent_name, room.name)
                seen.setdefault(
                    key,
                    AgentRow(d.agent_name, room.name, None, "dispatched", humans, None),
                )
        return sorted(seen.values(), key=lambda r: (r.agent_name, r.room))

    async def create_room(self, name: str) -> None:
        await self._api.room.create_room(_req("CreateRoomRequest", name=name))

    async def delete_room(self, name: str) -> None:
        await self._api.room.delete_room(_req("DeleteRoomRequest", room=name))

    async def create_dispatch(self, room: str, agent_name: str, metadata: str = "") -> None:
        await self._api.agent_dispatch.create_dispatch(
            _req("CreateAgentDispatchRequest", room=room, agent_name=agent_name, metadata=metadata)
        )

    def join_token(self, room: str, identity: str) -> str:
        return (
            api.AccessToken(self._creds.api_key, self._creds.api_secret)
            .with_identity(identity)
            .with_grants(api.VideoGrants(room_join=True, room=room))
            .to_jwt()
        )

    async def aclose(self) -> None:
        await self._api.aclose()


def api_client(creds: LiveKitCreds) -> Any:
    return api.LiveKitAPI(creds.url, creds.api_key, creds.api_secret)


def _req(name: str, **kwargs):
    return getattr(api, name)(**kwargs)
