"""OpenRTC integration: drive VoiceGateway cost tracking from session lifecycle.

``VoiceGatewayObserver`` implements OpenRTC's ``SessionObserver`` protocol
structurally (duck-typed, no runtime ``openrtc`` import) so an OpenRTC
``AgentPool`` can attach VoiceGateway to every live session with one line::

    from openrtc import AgentPool
    from voicegateway.openrtc import VoiceGatewayObserver

    pool = AgentPool(observers=[VoiceGatewayObserver(
        project="prod",
        collector_url="https://collector.example.com",
        virtual_key="vk_...",
    )])

One sink is built lazily per worker process and shared across every session.
``attach()`` flushes but never closes a passed sink, so the first session
ending leaves the shared sink usable for the rest. The observer holds only
config, so it pickles cleanly into OpenRTC ``process``-isolation workers; the
live sink is rebuilt in-worker on the first session.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from voicegateway.inference.session.attach import _build_default_sink, attach

if TYPE_CHECKING:
    from livekit.agents import AgentSession
    from openrtc import SessionInfo, SessionOutcome

    from voicegateway.services.sinks import Sink


class VoiceGatewayObserver:
    """OpenRTC ``SessionObserver`` that tracks per-call cost for every session.

    Attribution per session: ``project`` from this observer, ``agent_id`` from
    ``info.agent_name``, ``tenant_id`` from ``info.metadata['tenant']``.
    """

    def __init__(
        self,
        *,
        project: str = "default",
        collector_url: str | None = None,
        virtual_key: str | None = None,
        db_path: str | None = None,
    ) -> None:
        self._project = project
        self._collector_url = collector_url
        self._virtual_key = virtual_key
        self._db_path = db_path
        self._sink: Sink | None = None

    def _ensure_sink(self) -> Sink:
        # Must stay await-free between the check and the assignment so two
        # concurrent first-sessions on one event loop cannot both build a sink.
        if self._sink is None:
            self._sink = _build_default_sink(
                self._collector_url or os.environ.get("VOICEGW_COLLECTOR_URL"),
                self._virtual_key or os.environ.get("VOICEGW_VIRTUAL_KEY"),
                db_path=self._db_path,
            )
        return self._sink

    async def on_session_start(
        self, info: SessionInfo, session: AgentSession[Any]
    ) -> None:
        attach(
            session,
            project=self._project,
            agent_id=info.agent_name,
            tenant_id=info.metadata.get("tenant"),
            sink=self._ensure_sink(),
        )

    async def on_session_end(self, info: SessionInfo, outcome: SessionOutcome) -> None:
        if self._sink is not None:
            await self._sink.flush()

    def __getstate__(self) -> dict[str, Any]:
        state = self.__dict__.copy()
        state["_sink"] = None
        return state
