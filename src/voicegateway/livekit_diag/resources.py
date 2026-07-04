"""Sample the prober host's CPU/mem/net during a run so a report can flag when
the prober (not the SFU) is the bottleneck and estimate its sustainable client
count.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

_SATURATION_CPU = 85.0


@dataclass(frozen=True)
class ResourceReport:
    cpu_peak: float
    mem_peak_mb: float
    net_kbps_up: float
    saturated: bool
    per_client: dict
    sustainable_n: int | None


def _psutil_sampler():
    # Returns raw (cpu_pct, rss_mb, bytes_sent). report_for() does the net delta,
    # so the sampler stays trivial and correct. Tests inject their own sampler.
    import psutil

    proc = psutil.Process()

    def sample():
        return (
            psutil.cpu_percent(interval=None),
            proc.memory_info().rss / 1e6,
            psutil.net_io_counters().bytes_sent,
        )

    return sample


class ResourceMonitor:
    def __init__(self, sampler=None) -> None:
        self._sampler = sampler or _psutil_sampler()
        self._cpu: list[float] = []
        self._mem: list[float] = []
        self._net: list[int] = []
        self._task: asyncio.Task | None = None

    def tick(self) -> None:
        cpu, mem, net = self._sampler()
        self._cpu.append(cpu)
        self._mem.append(mem)
        self._net.append(net)

    async def start(self, interval: float = 1.0) -> None:
        async def _loop():
            while True:
                self.tick()
                await asyncio.sleep(interval)

        self._task = asyncio.ensure_future(_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    def report_for(self, n_clients: int) -> ResourceReport:
        cpu_peak = max(self._cpu, default=0.0)
        mem_peak = max(self._mem, default=0.0)
        net_kbps = 0.0
        if len(self._net) >= 2:
            net_kbps = max(0.0, (self._net[-1] - self._net[0]) * 8 / 1000 / len(self._net))
        n = max(1, n_clients)
        per = {"cpu_pct": round(cpu_peak / n, 3), "kbps_up": round(net_kbps / n, 1)}
        sustainable = None
        if per["cpu_pct"] > 0:
            sustainable = int(_SATURATION_CPU / per["cpu_pct"])
        return ResourceReport(
            cpu_peak=cpu_peak,
            mem_peak_mb=round(mem_peak, 1),
            net_kbps_up=round(net_kbps, 1),
            saturated=cpu_peak > _SATURATION_CPU,
            per_client=per,
            sustainable_n=sustainable,
        )
