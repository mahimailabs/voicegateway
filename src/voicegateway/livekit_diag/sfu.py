"""SFU connection-quality baseline and capacity ramp, using synthetic clients
talking to each other (no agent). Latency is a clock-sync-free data-channel
round-trip through the SFU; jitter/loss come from connection stats.

**Cold start is warmed, never measured.** The first message a client sends over
its data channel pays that session's TLS/ICE/DTLS setup, and a step takes
exactly ONE sample per client, so with no warm-up every sample is a cold one.
Measured on a live deployment, that read 129.3ms at 2 clients against 26.1ms at
10 and 25.7ms at 25: more clients measuring FASTER, which is backwards for a
capacity probe and only explainable by per-session setup cost landing in the
number. The steady state was ~26ms, so the two-client tier came out roughly 5x
high, and that tier is exactly the one ``gates.sfu_capacity_gate`` reads: a
healthy server failed its own capacity gate on a warm-up artifact. Every client
now sends one discarded ping before the measured round (see
:meth:`SfuProbe._warm_up`). The budget is untouched; raising it would have
hidden this rather than fixed it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from statistics import mean
from typing import Any

# What ``rtt_ms`` actually is, named beside the number so a step that measured
# nothing cannot be read as a fast one. Same vocabulary as the node-correlation
# summaries (``p95`` / ``max_of_n`` / ``not_measured``): name the statistic, and
# say ``not_measured`` rather than presenting an absence as a reading.
MEAN_OF_N = "mean_of_n"
NOT_MEASURED = "not_measured"


@dataclass(frozen=True)
class RampStep:
    clients: int
    rtt_ms: float
    loss_pct: float
    quality: str
    # How many ping round-trips ``rtt_ms`` is the mean of. Never counts the
    # discarded warm-up ping, and never counts a ping that did not come back.
    # Defaults to 0 so a hand-built step is not credited with evidence it does
    # not carry; every step this module measures sets it.
    samples: int = 0

    @property
    def rtt_stat(self) -> str:
        """The name of the statistic in ``rtt_ms``.

        ``mean_of_n`` over ``samples`` round-trips, or ``not_measured`` when
        none came back. A step with ``samples == 0`` measured nothing at all,
        and its ``rtt_ms`` of 0.0 is a placeholder, not a fast SFU.
        """
        return MEAN_OF_N if self.samples else NOT_MEASURED


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

    async def _warm_up(self, clients: list[Any]) -> None:
        """One discarded ping per client, before anything is measured.

        The first message over a client's data channel pays that session's
        TLS/ICE/DTLS setup, and :meth:`_measure` takes exactly ONE sample per
        client, so without this pass every sample carries setup cost.

        Warming, rather than dropping the first measured sample, is what this
        shape of measurement needs. Dropping one sample would leave the
        two-client baseline with a single reading (and a one-client tier with
        none) while STILL leaving every remaining client's own first-message
        cost inside the number, because each client only ever pings once.

        It runs before the soak, not immediately before the measured round, so
        that a pong still in flight from this pass has drained by the time the
        measurement starts: a stray pong completes the next ping early and would
        under-report the rtt, which is the one error worse than over-reporting
        it.
        """
        for c in clients:
            try:
                await c.ping()
            except Exception:  # noqa: BLE001 - a warm-up produces no number
                # so it must not be what ends a probe that can still measure. A
                # client that is genuinely broken fails its measured ping too,
                # and that failure is the honest one to report.
                pass

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
            await self._warm_up(clients)
            await asyncio.sleep(seconds)
            pings: list[float] = []
            for c in clients:
                rtt = await c.ping()
                if rtt is not None:
                    pings.append(rtt)
            rtt_ms = (mean(pings) * 1000) if pings else 0.0
            quality = clients[0].quality() if clients else "Unknown"
            # Loss is read from stats where available; default 0.0 when the SDK does
            # not expose it. quality carries the coarse signal regardless.
            return RampStep(n, round(rtt_ms, 1), 0.0, quality, len(pings))
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
