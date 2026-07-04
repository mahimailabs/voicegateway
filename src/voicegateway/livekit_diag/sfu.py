"""SFU connection-quality baseline and capacity ramp, using synthetic clients
talking to each other (no agent). Latency is a clock-sync-free data-channel
round-trip through the SFU; jitter/loss come from connection stats.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from statistics import mean


@dataclass(frozen=True)
class RampStep:
    clients: int
    rtt_ms: float
    loss_pct: float
    quality: str


def find_knee(
    steps: list[RampStep], target_rtt_ms: float, max_loss: float
) -> int | None:
    """The last healthy client count before the first step that breaks a
    threshold. None when every step is within budget.
    """
    last_ok = None
    for s in steps:
        if s.rtt_ms > target_rtt_ms or s.loss_pct > max_loss:
            return last_ok
        last_ok = s.clients
    return None


class SfuProbe:
    def __init__(self, admin, client_factory, monitor) -> None:
        self._admin = admin
        self._make_client = client_factory
        self._monitor = monitor

    async def _measure(
        self, room: str, n: int, seconds: float, *, cleanup: bool = True
    ) -> RampStep:
        clients = []
        try:
            for i in range(n):
                c = self._make_client(
                    getattr(self._admin, "url", ""),
                    self._admin.join_token(room, f"c{i}"),
                )
                await c.connect()
                clients.append(c)
            await asyncio.sleep(seconds)
            pings = [p for c in clients for p in [await c.ping()] if p is not None]
            rtt_ms = (mean(pings) * 1000) if pings else 0.0
            quality = clients[0].quality() if clients else "Unknown"
            # Loss is read from stats where available; default 0.0 when the SDK does
            # not expose it. quality carries the coarse signal regardless.
            return RampStep(n, round(rtt_ms, 1), 0.0, quality)
        finally:
            for c in clients:
                await c.disconnect()
            # Delete the probe room so it does not linger on the server and show up
            # as a phantom (empty-name) agent in a later list_agents. Best-effort.
            # Skipped for a shared distributed room, where one vantage deleting it
            # mid-measurement would drop the other vantages' clients; the
            # coordinator cleans those up after every vantage has reported.
            if cleanup:
                await self._admin.delete_room(room)

    async def baseline(self, room: str, seconds: float = 10.0) -> RampStep:
        return await self._measure(room, 2, seconds)

    async def ramp(
        self,
        room: str,
        steps: list[int],
        duration: float,
        target_rtt_ms: float,
        max_loss: float,
        *,
        cleanup: bool = True,
    ):
        await self._monitor.start()
        results = []
        try:
            for n in steps:
                results.append(
                    await self._measure(f"{room}-{n}", n, duration, cleanup=cleanup)
                )
        finally:
            await self._monitor.stop()
        return results, self._monitor.report_for(max(steps) if steps else 0)
