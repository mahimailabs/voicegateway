"""The only unit that talks to the LiveKit server API. Owns credentials.

Wraps livekit.api for the read paths (rooms, participants, dispatches) the
diagnostics need, plus create/delete room + dispatch for the latency probe, and
join-token minting for the synthetic client.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from livekit import api
from livekit.protocol.models import ParticipantInfo

from voicegateway.livekit_diag.config import LiveKitCreds

logger = logging.getLogger(__name__)

_AGENT_KIND = ParticipantInfo.Kind.AGENT


@dataclass(frozen=True)
class AgentRow:
    agent_name: str
    room: str
    identity: str | None
    state: str  # "active" (joined) or "dispatched" (assigned, not joined)
    humans: int
    age_s: float | None


# The control-plane component rows below deliberately carry only non-sensitive
# identifiers. LiveKit's egress/ingress/sip info messages also hold secrets
# (auth_password, auth_username, stream_key, url, allowed_addresses, headers);
# those are NEVER copied onto these rows, so the dashboard cannot leak them.
@dataclass(frozen=True)
class EgressRow:
    egress_id: str
    status: str  # EgressStatus name, e.g. "EGRESS_ACTIVE"
    source_type: str  # EgressSourceType name
    room_name: str
    started_at: int  # unix ns (0 when unset)


@dataclass(frozen=True)
class IngressRow:
    ingress_id: str
    name: str
    input_type: str  # IngressInput name, e.g. "RTMP_INPUT"
    room_name: str
    status: str  # IngressState.Status name, "" when no state reported


@dataclass(frozen=True)
class SipInboundTrunkRow:
    trunk_id: str
    name: str
    numbers: list[str]


@dataclass(frozen=True)
class SipOutboundTrunkRow:
    trunk_id: str
    name: str
    address: str
    transport: str  # SIPTransport name
    numbers: list[str]


@dataclass(frozen=True)
class SipDispatchRuleRow:
    rule_id: str
    name: str
    trunk_ids: list[str]


class LiveKitAdmin:
    def __init__(self, creds: LiveKitCreds, *, api: Any | None = None) -> None:
        self._creds = creds
        self._api = api or api_client(creds)

    async def list_agents(self) -> list[AgentRow]:
        now = time.time()
        rooms = (await self._api.room.list_rooms(_req("ListRoomsRequest"))).rooms
        seen: dict[tuple[str, str], AgentRow] = {}
        for room in rooms:
            try:
                parts = (
                    await self._api.room.list_participants(
                        _req("ListParticipantsRequest", room=room.name)
                    )
                ).participants
                # list_dispatch takes the room NAME (str) and returns a list directly.
                dispatches = await self._api.agent_dispatch.list_dispatch(room.name)
            except Exception:  # noqa: BLE001
                # A room can close (empty rooms auto-close) between list_rooms and
                # these per-room queries; a vanished room is not an error, just skip it.
                continue
            humans = sum(1 for p in parts if p.kind != _AGENT_KIND)
            for p in parts:
                name = p.name or p.identity
                if p.kind == _AGENT_KIND and name:
                    key = (name, room.name)
                    joined = getattr(p, "joined_at", 0) or 0
                    seen[key] = AgentRow(
                        agent_name=name,
                        room=room.name,
                        identity=p.identity,
                        state="active",
                        humans=humans,
                        age_s=(now - joined) if joined else None,
                    )
            for d in dispatches:
                # Skip empty-named dispatch records (a lingering probe room can
                # report one); an agent without a name is not a real agent.
                if not d.agent_name:
                    continue
                key = (d.agent_name, room.name)
                seen.setdefault(
                    key,
                    AgentRow(d.agent_name, room.name, None, "dispatched", humans, None),
                )
        return sorted(seen.values(), key=lambda r: (r.agent_name, r.room))

    # --- Control-plane component reads (read-only, non-billing) --------------

    async def list_egress(self) -> list[EgressRow]:
        resp = await self._api.egress.list_egress(_req("ListEgressRequest"))
        return [
            EgressRow(
                egress_id=e.egress_id,
                status=_enum_name(e, "status"),
                source_type=_enum_name(e, "source_type"),
                room_name=e.room_name,
                started_at=e.started_at,
            )
            for e in resp.items
        ]

    async def list_ingress(self) -> list[IngressRow]:
        resp = await self._api.ingress.list_ingress(_req("ListIngressRequest"))
        return [
            IngressRow(
                ingress_id=i.ingress_id,
                name=i.name,
                input_type=_enum_name(i, "input_type"),
                room_name=i.room_name,
                status=_enum_name(i.state, "status") if i.HasField("state") else "",
            )
            for i in resp.items
        ]

    async def list_sip_inbound_trunks(self) -> list[SipInboundTrunkRow]:
        resp = await self._api.sip.list_sip_inbound_trunk(
            _req("ListSIPInboundTrunkRequest")
        )
        return [
            SipInboundTrunkRow(
                trunk_id=t.sip_trunk_id, name=t.name, numbers=list(t.numbers)
            )
            for t in resp.items
        ]

    async def list_sip_outbound_trunks(self) -> list[SipOutboundTrunkRow]:
        resp = await self._api.sip.list_sip_outbound_trunk(
            _req("ListSIPOutboundTrunkRequest")
        )
        return [
            SipOutboundTrunkRow(
                trunk_id=t.sip_trunk_id,
                name=t.name,
                address=t.address,
                transport=_enum_name(t, "transport"),
                numbers=list(t.numbers),
            )
            for t in resp.items
        ]

    async def list_sip_dispatch_rules(self) -> list[SipDispatchRuleRow]:
        resp = await self._api.sip.list_sip_dispatch_rule(
            _req("ListSIPDispatchRuleRequest")
        )
        return [
            SipDispatchRuleRow(
                rule_id=r.sip_dispatch_rule_id, name=r.name, trunk_ids=list(r.trunk_ids)
            )
            for r in resp.items
        ]

    async def create_room(self, name: str) -> None:
        await self._api.room.create_room(_req("CreateRoomRequest", name=name))

    async def delete_room(self, name: str) -> None:
        # Best-effort cleanup: LiveKit auto-closes a room when its last participant
        # leaves, so a delete can race to "room does not exist". That is success for
        # our purposes (the room is gone), and this runs in probe finally blocks, so
        # it must never raise and abort the caller.
        try:
            await self._api.room.delete_room(_req("DeleteRoomRequest", room=name))
        except Exception:  # noqa: BLE001
            logger.debug(
                "delete_room(%s): ignored (room already gone?)", name, exc_info=True
            )

    async def create_dispatch(
        self, room: str, agent_name: str, metadata: str = ""
    ) -> None:
        await self._api.agent_dispatch.create_dispatch(
            _req(
                "CreateAgentDispatchRequest",
                room=room,
                agent_name=agent_name,
                metadata=metadata,
            )
        )

    async def room_participant_identities(self, room: str) -> list[str]:
        """Identities currently in ``room``.

        The probe uses it to confirm a dispatched worker actually joined: LiveKit
        has no "did this dispatch find a worker" call, but a worker that took the
        job shows up here as a participant. An empty read (only the probe's own
        caller present) means nobody answered.
        """
        resp = await self._api.room.list_participants(
            _req("ListParticipantsRequest", room=room)
        )
        return [p.identity for p in resp.participants]

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


def _req(cls_name: str, **kwargs):
    # The param name must not collide with request fields (e.g. CreateRoomRequest
    # has a `name` field), so it is cls_name, not name.
    return getattr(api, cls_name)(**kwargs)


def _enum_name(msg: Any, field: str) -> str:
    """Human-readable name for an enum field on a proto message.

    Falls back to the raw integer (as a string) for a value the descriptor does
    not know, so a newer server enum never raises here.
    """
    value = getattr(msg, field)
    descriptor = msg.DESCRIPTOR.fields_by_name.get(field)
    enum_type = descriptor.enum_type if descriptor is not None else None
    if enum_type is None:
        return str(value)
    entry = enum_type.values_by_number.get(value)
    return entry.name if entry is not None else str(value)
