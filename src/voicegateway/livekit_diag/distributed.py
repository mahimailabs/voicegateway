"""Distributed SFU load: many prober vantages hammer one room together.

A single-host ``voicegw livekit sfu --load`` only shows what one machine can
push through the SFU. Real capacity questions ("does my SFU hold up when 200
clients from four regions join at once?") need load applied concurrently from
several vantages. This module coordinates that:

- a **coordinator** (``sfu --coordinator --expect N``) hands out one shared job
  (room, ramp tiers, duration, thresholds) and a synchronized ``start_at``, then
  collects each vantage's per-tier measurements and aggregates them;
- a **prober** (``sfu --report-to URL --vantage LABEL``) registers, waits for the
  barrier, runs the ramp against the shared room, and reports back.

The intellectual core (the barrier + aggregation) is plain and unit-tested here;
the HTTP layer is a thin FastAPI adapter, and the real multi-region run is a
deploy exercise (see ``deploy/prober/`` and the distributed-SFU docs).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

# Worst-to-best quality ranking; the aggregate reports the worst any vantage saw
# at a tier. "Unknown" sits above the healthy states but below the bad ones: it
# is a missing signal, not evidence of failure.
_QUALITY_RANK = {"Excellent": 0, "Good": 1, "Unknown": 2, "Poor": 3, "Lost": 4}


class RegisterBody(BaseModel):
    """POST /register body. Module-level so FastAPI can resolve the annotation
    under ``from __future__ import annotations`` (get_type_hints reads module
    globals, not a function's local scope)."""

    vantage: str | None = None


class ReportBody(BaseModel):
    """POST /report/{prover_id} body: one vantage's per-tier steps."""

    vantage: str
    steps: list[dict]
    baseline: dict | None = None


@dataclass
class VantageReport:
    """One prover's ramp result: per-tier steps in ramp order, plus its label."""

    vantage: str
    steps: list[dict]  # each: {clients, rtt_ms, loss_pct, quality}
    baseline: dict | None = None


def _worst_quality(qualities: list[str]) -> str:
    if not qualities:
        return "Unknown"
    return max(qualities, key=lambda q: _QUALITY_RANK.get(q, 2))


def aggregate_vantages(
    reports: list[VantageReport], target_rtt_ms: float, max_loss: float
) -> dict[str, Any]:
    """Fold N vantage reports into one combined capacity view.

    Every vantage runs the SAME ramp tiers, so tier *index* i is the comparable
    unit: the SFU sees the SUM of each vantage's client count at tier i, while
    rtt / loss / quality take the WORST any single vantage observed (the SFU is
    only as healthy as its unhappiest vantage). The combined knee is the last
    tier whose total client count stayed within both thresholds. Vantages that
    reported short (a failed tier) only contribute the tiers they have.
    """
    valid = [r for r in reports if r.steps]
    tier_count = min((len(r.steps) for r in valid), default=0)
    combined: list[dict[str, Any]] = []
    for i in range(tier_count):
        row = [r.steps[i] for r in valid]
        combined.append(
            {
                "tier": i,
                "clients": sum(int(s.get("clients", 0)) for s in row),
                "vantages": len(row),
                "rtt_ms": max((float(s.get("rtt_ms", 0.0)) for s in row), default=0.0),
                "loss_pct": max(
                    (float(s.get("loss_pct", 0.0)) for s in row), default=0.0
                ),
                "quality": _worst_quality(
                    [str(s.get("quality", "Unknown")) for s in row]
                ),
            }
        )

    # Knee: the last healthy total client count BEFORE the first tier that breaks
    # a threshold. Matches single-host find_knee semantics exactly, including None
    # when no tier breaks ("no knee within ramp: sustained the whole ramp"), so
    # the two SFU modes never report contradictory things for an all-healthy run.
    knee: int | None = None
    last_ok: int | None = None
    for tier in combined:
        if tier["rtt_ms"] > target_rtt_ms or tier["loss_pct"] > max_loss:
            knee = last_ok
            break
        last_ok = tier["clients"]

    return {
        "vantages": [{"vantage": r.vantage, "steps": r.steps} for r in valid],
        "combined": combined,
        "knee": knee,
        "target_rtt_ms": target_rtt_ms,
        "max_loss": max_loss,
        "dropped": [r.vantage for r in reports if not r.steps],
    }


class Coordinator:
    """Barrier + report sink for a distributed SFU run. HTTP-agnostic.

    Provers ``register``; once ``expect`` of them have, the coordinator fixes a
    shared ``start_at`` a couple seconds out so every vantage begins its ramp at
    the same wall-clock instant. Each prover then ``submit_report``s its steps;
    ``result`` aggregates once ``all_reported``.
    """

    def __init__(
        self,
        *,
        expect: int,
        room: str,
        ramp: list[int],
        duration: float,
        target_rtt_ms: float,
        max_loss: float,
        start_delay: float = 3.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._expect = expect
        self._room = room
        self._ramp = list(ramp)
        self._duration = duration
        self._target_rtt_ms = target_rtt_ms
        self._max_loss = max_loss
        self._start_delay = start_delay
        self._clock = clock
        self._registered: list[str | None] = []
        self._reports: dict[int, VantageReport] = {}
        self._start_at: float | None = None

    def register(self, vantage: str | None = None) -> dict[str, Any]:
        """Admit one prover; arm the shared start once the last expected joins."""
        prover_id = len(self._registered)
        self._registered.append(vantage)
        if len(self._registered) >= self._expect and self._start_at is None:
            self._start_at = self._clock() + self._start_delay
        return {"prover_id": prover_id, "expected": self._expect}

    def job_for(self, prover_id: int) -> dict[str, Any]:
        """The shared job. ``ready`` flips true once the barrier is armed."""
        return {
            "room": self._room,
            "ramp": self._ramp,
            "duration": self._duration,
            "target_rtt_ms": self._target_rtt_ms,
            "max_loss": self._max_loss,
            "start_at": self._start_at,
            "ready": self._start_at is not None,
        }

    def submit_report(self, prover_id: int, report: VantageReport) -> None:
        self._reports[prover_id] = report

    @property
    def all_reported(self) -> bool:
        return self._expect > 0 and len(self._reports) >= self._expect

    @property
    def rooms(self) -> list[str]:
        """The shared per-tier room names, for post-run cleanup."""
        return [f"{self._room}-{n}" for n in self._ramp]

    def result(self) -> dict[str, Any]:
        return aggregate_vantages(
            list(self._reports.values()), self._target_rtt_ms, self._max_loss
        )


def build_coordinator_app(coordinator: Coordinator) -> Any:
    """Wrap a :class:`Coordinator` in a FastAPI app (register/job/report/result).

    FastAPI lives under the ``[server]`` extra, so it is imported lazily: the
    prober path needs only core ``httpx``.
    """
    try:
        from fastapi import FastAPI
    except ImportError as exc:  # pragma: no cover - import guard
        raise RuntimeError(
            "the SFU coordinator needs the server extra: pip install 'voicegateway[dashboard]'"
        ) from exc

    app = FastAPI(title="voicegateway SFU coordinator")

    @app.post("/register")
    async def register(body: RegisterBody) -> dict[str, Any]:
        return coordinator.register(body.vantage)

    @app.get("/job/{prover_id}")
    async def job(prover_id: int) -> dict[str, Any]:
        return coordinator.job_for(prover_id)

    @app.post("/report/{prover_id}")
    async def report(prover_id: int, body: ReportBody) -> dict[str, Any]:
        coordinator.submit_report(
            prover_id, VantageReport(body.vantage, body.steps, body.baseline)
        )
        return {"ok": True, "all_reported": coordinator.all_reported}

    @app.get("/result")
    async def result() -> dict[str, Any]:
        return coordinator.result()

    return app


async def serve_coordinator(
    coordinator: Coordinator,
    *,
    host: str = "0.0.0.0",
    port: int = 8787,
    timeout: float = 600.0,
) -> dict[str, Any]:
    """Serve the coordinator until every prover has reported, then aggregate.

    Stops after ``timeout`` seconds even if some provers never report, so a
    crashed or missing prover cannot hang the run forever: it aggregates whatever
    did arrive (``result()["dropped"]`` and a short roster tell the caller who is
    missing). uvicorn (also ``[server]``) is imported lazily like FastAPI.
    Returns the aggregated result; the caller renders + cleans up.
    """
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - import guard
        raise RuntimeError(
            "the SFU coordinator needs the server extra: pip install 'voicegateway[dashboard]'"
        ) from exc

    app = build_coordinator_app(coordinator)
    server = uvicorn.Server(
        uvicorn.Config(app, host=host, port=port, log_level="warning")
    )

    async def _watch() -> None:
        waited = 0.0
        while not coordinator.all_reported and waited < timeout:
            await asyncio.sleep(0.5)
            waited += 0.5
        server.should_exit = True

    watcher = asyncio.ensure_future(_watch())
    try:
        await server.serve()
    finally:
        watcher.cancel()
    return coordinator.result()


@dataclass
class _Http:
    """Tiny httpx wrapper so run_prober is testable with a fake transport."""

    base_url: str
    _client: Any = field(default=None)

    async def __aenter__(self) -> _Http:
        import httpx

        self._client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def post(self, path: str, json: dict) -> dict:
        resp = await self._client.post(self.base_url.rstrip("/") + path, json=json)
        resp.raise_for_status()
        body: dict = resp.json()
        return body

    async def get(self, path: str) -> dict:
        resp = await self._client.get(self.base_url.rstrip("/") + path)
        resp.raise_for_status()
        body: dict = resp.json()
        return body


async def run_prober(
    report_to: str,
    probe: Any,
    *,
    vantage: str,
    http: Any = None,
    sleep: Callable[[float], Any] = asyncio.sleep,
    clock: Callable[[], float] = time.time,
    poll_interval: float = 1.0,
    barrier_timeout: float = 300.0,
) -> dict[str, Any]:
    """Register with the coordinator, wait for the barrier, ramp, report.

    ``http`` (an object with async ``post(path, json)`` / ``get(path)``), ``sleep``
    and ``clock`` are injectable so the flow is unit-testable without a live
    coordinator or SFU. Runs the ramp with ``cleanup=False``: the room is shared,
    so the coordinator deletes it after all vantages report. Gives up at the
    barrier after ``barrier_timeout`` seconds (raises) rather than waiting forever
    when a peer prover never registers.
    """
    own_http = http is None
    client = http if http is not None else _Http(report_to)
    if own_http:
        await client.__aenter__()
    try:
        reg = await client.post("/register", {"vantage": vantage})
        prover_id = reg["prover_id"]

        max_polls = max(1, int(barrier_timeout / poll_interval)) if poll_interval else 1
        polls = 0
        while True:
            job = await client.get(f"/job/{prover_id}")
            if job.get("ready"):
                break
            polls += 1
            if polls >= max_polls:
                raise RuntimeError(
                    f"barrier timed out after ~{barrier_timeout}s waiting for all "
                    f"{reg.get('expected', '?')} probers to register"
                )
            await sleep(poll_interval)

        start_at = job.get("start_at")
        if start_at is not None:
            delay = float(start_at) - clock()
            if delay > 0:
                await sleep(delay)

        ramp = job["ramp"]
        counts = (
            [int(x) for x in str(ramp).split(",") if str(x).strip()]
            if isinstance(ramp, str)
            else [int(x) for x in ramp]
        )
        steps, _resource = await probe.ramp(
            job["room"],
            counts,
            float(job["duration"]),
            float(job["target_rtt_ms"]),
            float(job["max_loss"]),
            cleanup=False,
        )
        payload = {
            "vantage": vantage,
            "steps": [
                {
                    "clients": s.clients,
                    "rtt_ms": s.rtt_ms,
                    "loss_pct": s.loss_pct,
                    "quality": s.quality,
                }
                for s in steps
            ],
        }
        await client.post(f"/report/{prover_id}", payload)
        return payload
    finally:
        if own_http:
            await client.__aexit__(None, None, None)


__all__ = [
    "Coordinator",
    "VantageReport",
    "aggregate_vantages",
    "build_coordinator_app",
    "run_prober",
    "serve_coordinator",
]
