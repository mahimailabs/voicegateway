"""Active per-agent latency probe: dispatch the agent to a throwaway room, call
it with a synthetic client, time the reply, and (when the agent is instrumented)
read the STT/LLM/TTS + turn-detection split from VoiceGateway telemetry.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from statistics import mean


@dataclass
class LatencyResult:
    agent: str
    e2e_samples: list[float] = field(default_factory=list)
    network_s: float | None = None
    components: dict | None = None
    error: str | None = None


class ComponentReader:
    """Reads the probe session's component + EOU rows from a VG store. Optional;
    returns None when no store is configured or the agent is not instrumented.
    """

    def __init__(self, store=None) -> None:
        self._store = store

    async def read(self, room: str) -> dict | None:
        if self._store is None:
            return None
        return await self._store.components_for_room(room)


class ProbeRunner:
    def __init__(self, admin, client_factory, utterance, reader: ComponentReader | None = None) -> None:
        self._admin = admin
        self._url = getattr(admin, "url", "")
        self._make_client = client_factory
        self._utterance = utterance
        self._reader = reader or ComponentReader()

    async def probe(self, agent: str, trials: int, warmup: bool, room_name: str | None, metadata: str) -> LatencyResult:
        result = LatencyResult(agent=agent)
        total = trials + (1 if warmup else 0)
        for i in range(total):
            room = room_name or f"vg-probe-{agent}-{uuid.uuid4().hex[:8]}"
            e2e = None
            try:
                await self._admin.create_room(room)
                await self._admin.create_dispatch(room, agent, metadata)
                client = self._make_client(self._url, self._admin.join_token(room, "vg-probe"))
                await client.connect()
                try:
                    t0 = await client.publish_utterance(self._utterance)
                    e2e = await client.wait_reply(t0)
                    if result.network_s is None:
                        result.network_s = await client.ping()
                finally:
                    await client.disconnect()
            finally:
                await self._admin.delete_room(room)
            if i == 0 and warmup:
                continue  # discard cold start
            if e2e is not None:
                result.e2e_samples.append(e2e)
                if result.components is None:
                    result.components = await self._reader.read(room)
        return result


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = min(len(s) - 1, int(round((p / 100) * (len(s) - 1))))
    return s[k]


def summarize(result: LatencyResult) -> dict:
    xs = result.e2e_samples
    return {
        "avg": mean(xs) if xs else 0.0,
        "p50": _pct(xs, 50),
        "p95": _pct(xs, 95),
        "min": min(xs) if xs else 0.0,
        "max": max(xs) if xs else 0.0,
        "trials": len(xs),
    }
