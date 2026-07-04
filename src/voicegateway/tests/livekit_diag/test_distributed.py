"""Distributed SFU: the barrier + aggregation core, the HTTP contract, and the
prober flow. The real multi-region run is a deploy exercise; here we prove the
logic that stitches vantages together.
"""

from __future__ import annotations

from typing import Any

import pytest

from voicegateway.livekit_diag.distributed import (
    Coordinator,
    VantageReport,
    aggregate_vantages,
    build_coordinator_app,
    run_prober,
)
from voicegateway.livekit_diag.sfu import RampStep


def _steps(rtts, *, clients=(2, 5), loss=0.0, quality="Good"):
    # strict=False on purpose: pass fewer rtts than client tiers to model a
    # vantage that reported short (the ragged-report case).
    return [
        {"clients": c, "rtt_ms": r, "loss_pct": loss, "quality": quality}
        for c, r in zip(clients, rtts, strict=False)
    ]


# --- aggregation ---------------------------------------------------------


def test_aggregate_sums_clients_and_takes_worst_rtt():
    reports = [
        VantageReport("iad", _steps([10.0, 20.0])),
        VantageReport("sjc", _steps([12.0, 40.0])),
    ]
    out = aggregate_vantages(reports, target_rtt_ms=50.0, max_loss=1.0)
    assert [t["clients"] for t in out["combined"]] == [4, 10]  # 2+2, 5+5
    assert out["combined"][0]["vantages"] == 2
    assert out["combined"][1]["rtt_ms"] == 40.0  # worst of 20/40
    # No tier breaks -> no knee (sustained the whole ramp), matching find_knee.
    assert out["knee"] is None


def test_aggregate_knee_stops_at_first_broken_tier():
    reports = [
        VantageReport("iad", _steps([10.0, 80.0])),  # tier 2 blows target
        VantageReport("sjc", _steps([12.0, 15.0])),
    ]
    out = aggregate_vantages(reports, target_rtt_ms=50.0, max_loss=1.0)
    assert out["knee"] == 4  # last healthy total (tier 1) before the break


def test_aggregate_worst_quality_wins():
    reports = [
        VantageReport("iad", _steps([10.0], clients=(2,), quality="Excellent")),
        VantageReport("sjc", _steps([10.0], clients=(2,), quality="Poor")),
    ]
    out = aggregate_vantages(reports, target_rtt_ms=50.0, max_loss=1.0)
    assert out["combined"][0]["quality"] == "Poor"


def test_aggregate_ragged_reports_use_common_tier_count_and_flag_empty():
    reports = [
        VantageReport("iad", _steps([10.0, 20.0])),  # 2 tiers
        VantageReport("sjc", _steps([12.0])),  # only 1 tier
        VantageReport("lhr", []),  # reported nothing
    ]
    out = aggregate_vantages(reports, target_rtt_ms=50.0, max_loss=1.0)
    assert len(out["combined"]) == 1  # min common tier count among valid vantages
    assert out["dropped"] == ["lhr"]


# --- coordinator barrier -------------------------------------------------


def test_coordinator_arms_start_only_when_full():
    clock = [1000.0]
    coord = Coordinator(
        expect=2,
        room="vg",
        ramp=[2, 5],
        duration=1.0,
        target_rtt_ms=50.0,
        max_loss=1.0,
        start_delay=3.0,
        clock=lambda: clock[0],
    )
    r0 = coord.register("iad")
    assert r0 == {"prover_id": 0, "expected": 2}
    assert coord.job_for(0)["ready"] is False  # not full yet
    coord.register("sjc")
    job = coord.job_for(1)
    assert job["ready"] is True
    assert job["start_at"] == 1003.0  # clock + start_delay
    assert job["ramp"] == [2, 5]


def test_coordinator_all_reported_and_result_and_rooms():
    coord = Coordinator(
        expect=2,
        room="vg",
        ramp=[2, 5],
        duration=1.0,
        target_rtt_ms=50.0,
        max_loss=1.0,
    )
    coord.register("iad")
    coord.register("sjc")
    assert coord.all_reported is False
    coord.submit_report(0, VantageReport("iad", _steps([10.0, 20.0])))
    assert coord.all_reported is False
    coord.submit_report(1, VantageReport("sjc", _steps([12.0, 22.0])))
    assert coord.all_reported is True
    assert coord.rooms == ["vg-2", "vg-5"]  # shared per-tier rooms
    assert [t["clients"] for t in coord.result()["combined"]] == [4, 10]


# --- HTTP contract (ASGI, no port) ---------------------------------------


async def test_coordinator_app_register_job_report_result():
    pytest.importorskip("fastapi")
    from httpx import ASGITransport, AsyncClient

    coord = Coordinator(
        expect=1,
        room="vg",
        ramp=[2],
        duration=1.0,
        target_rtt_ms=50.0,
        max_loss=1.0,
        start_delay=0.0,
        clock=lambda: 500.0,
    )
    app = build_coordinator_app(coord)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        reg = (await c.post("/register", json={"vantage": "iad"})).json()
        assert reg == {"prover_id": 0, "expected": 1}
        job = (await c.get("/job/0")).json()
        assert job["ready"] is True and job["room"] == "vg"
        rep = await c.post(
            "/report/0",
            json={"vantage": "iad", "steps": _steps([10.0], clients=(2,))},
        )
        assert rep.json() == {"ok": True, "all_reported": True}
        result = (await c.get("/result")).json()
    assert result["combined"][0]["clients"] == 2


# --- prober flow ---------------------------------------------------------


class _FakeHttp:
    """Stands in for the coordinator: not-ready once, then ready."""

    def __init__(self, ready_after: int = 2) -> None:
        self.posts: list[tuple[str, dict]] = []
        self._job_calls = 0
        self._ready_after = ready_after

    async def post(self, path: str, json: dict) -> dict:
        self.posts.append((path, json))
        if path == "/register":
            return {"prover_id": 0, "expected": 1}
        return {"ok": True, "all_reported": True}

    async def get(self, path: str) -> dict:
        self._job_calls += 1
        ready = self._job_calls >= self._ready_after
        return {
            "room": "vg-sfu-probe",
            "ramp": [2, 5],
            "duration": 1.0,
            "target_rtt_ms": 50.0,
            "max_loss": 1.0,
            "start_at": 100.0 if ready else None,
            "ready": ready,
        }


class _FakeProbe:
    def __init__(self) -> None:
        self.ramp_calls: list[Any] = []

    async def ramp(self, room, counts, duration, target, max_loss, *, cleanup):
        self.ramp_calls.append({"room": room, "counts": counts, "cleanup": cleanup})
        steps = [RampStep(n, 10.0 * i, 0.0, "Good") for i, n in enumerate(counts, 1)]
        return steps, None


async def test_run_prober_waits_reports_and_disables_cleanup():
    http = _FakeHttp(ready_after=2)
    probe = _FakeProbe()
    slept: list[float] = []

    async def _sleep(d):
        slept.append(d)

    payload = await run_prober(
        "http://coord",
        probe,
        vantage="iad",
        http=http,
        sleep=_sleep,
        clock=lambda: 100.0,  # start_at 100 -> no start wait
        poll_interval=0.5,
    )

    # registered, polled job until ready, reported.
    assert http.posts[0] == ("/register", {"vantage": "iad"})
    assert http.posts[-1][0] == "/report/0"
    assert http.posts[-1][1]["vantage"] == "iad"
    assert payload["steps"][0]["clients"] == 2
    # ramp ran against the shared room with cleanup disabled.
    assert probe.ramp_calls[0]["room"] == "vg-sfu-probe"
    assert probe.ramp_calls[0]["counts"] == [2, 5]
    assert probe.ramp_calls[0]["cleanup"] is False
    # slept once for the not-ready poll; start delay was <= 0 so not again.
    assert slept == [0.5]


class _NeverReadyHttp:
    """A coordinator whose barrier never arms (a peer prover never registers)."""

    async def post(self, path, json):
        return {"prover_id": 0, "expected": 2}

    async def get(self, path):
        return {"ready": False, "start_at": None}


async def test_run_prober_barrier_times_out_instead_of_hanging():
    async def _sleep(_d):
        pass

    with pytest.raises(RuntimeError, match="barrier timed out"):
        await run_prober(
            "http://coord",
            _FakeProbe(),
            vantage="iad",
            http=_NeverReadyHttp(),
            sleep=_sleep,
            clock=lambda: 0.0,
            poll_interval=1.0,
            barrier_timeout=3.0,  # -> at most 3 polls, then raise
        )
