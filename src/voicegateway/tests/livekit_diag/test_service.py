from __future__ import annotations

import asyncio

from voicegateway.livekit_diag import service as probes


def test_clamp_config_enforces_caps():
    out = probes.clamp_config({"ramp": [2, 10, 100], "duration": 120, "trials": 9})
    assert max(out["ramp"]) <= probes.MAX_LOAD_CLIENTS
    assert out["duration"] <= probes.MAX_LOAD_SECONDS
    assert out["trials"] <= probes.MAX_LATENCY_TRIALS
    # total load time must be bounded
    assert len(out["ramp"]) * out["duration"] <= probes.MAX_LOAD_SECONDS


def test_clamp_config_tolerates_bad_input():
    # A non-numeric target_ms and a non-iterable ramp must not 500.
    out = probes.clamp_config(
        {"duration": "abc", "trials": -5, "ramp": 5, "target_ms": "fast"}
    )
    assert out["duration"] >= 1.0
    assert out["duration"] <= probes.MAX_LOAD_SECONDS
    assert out["trials"] >= 1
    assert isinstance(out["target_ms"], float)  # bad target_ms fell back to default
    assert isinstance(out["ramp"], list) and out["ramp"]  # non-iterable -> default
    for n in out["ramp"]:
        assert 0 < n <= probes.MAX_LOAD_CLIENTS


def test_clamp_config_drops_bad_ramp_elements():
    out = probes.clamp_config({"ramp": ["x", 50]})
    for n in out["ramp"]:
        assert 0 < n <= probes.MAX_LOAD_CLIENTS


class _FakeProbes:
    def __init__(self):
        self.calls = []

    async def agents(self, creds):
        self.calls.append("agents")
        return {"agents": [{"agent_name": "a", "room": "r"}]}

    async def sfu(self, creds, load, config):
        self.calls.append(("sfu", load))
        return {
            "baseline": {"rtt_ms": 11, "loss_pct": 0.0, "quality": "Excellent"},
            "ramp": [],
            "knee": None,
        }

    async def latency(self, creds, config):
        self.calls.append("latency")
        # Use the real structure that _verdict and RealProbes.latency produce
        return {
            "agents": [
                {"agent": "a", "stats": {"avg": 0.8, "p95": 0.9}, "components": None}
            ]
        }


async def test_execute_run_runs_requested_checks_and_verdicts():
    fake = _FakeProbes()
    out = await probes.execute_run(
        ["agents", "sfu", "latency"], {"target_ms": 1500}, creds=object(), probes=fake
    )
    assert set(out["checks"]) == {"agents", "sfu", "latency"}
    assert out["verdict"] == "PASS"
    assert "agents" in fake.calls and "latency" in fake.calls


async def test_execute_run_isolates_a_failing_check():
    class _Boom(_FakeProbes):
        async def latency(self, creds, config):
            raise RuntimeError("no agents answered")

    out = await probes.execute_run(
        ["agents", "latency"], {}, creds=object(), probes=_Boom()
    )
    assert out["checks"]["agents"]["ok"] is True
    assert out["checks"]["latency"]["ok"] is False
    assert "no agents answered" in out["checks"]["latency"]["error"]
    assert out["verdict"] == "FAIL"


async def test_execute_run_times_out_slow_check(monkeypatch):
    """A check that hangs longer than PER_CHECK_TIMEOUT_SECONDS is aborted."""
    monkeypatch.setattr(probes, "PER_CHECK_TIMEOUT_SECONDS", 0.01)

    class _SlowProbes:
        async def agents(self, creds):
            await asyncio.sleep(10)
            return {"agents": []}

    out = await probes.execute_run(["agents"], {}, creds=object(), probes=_SlowProbes())
    assert out["checks"]["agents"] == {"ok": False, "error": "check timed out"}
    assert out["verdict"] == "FAIL"


async def test_verdict_warns_on_slow_latency():
    """Latency avg above target_ms yields WARN, not FAIL."""

    class _SlowLatencyProbes(_FakeProbes):
        async def latency(self, creds, config):
            return {
                "agents": [
                    {
                        "agent": "a",
                        "stats": {"avg": 2.0, "p95": 2.5},
                        "components": None,
                    }
                ]
            }

    out = await probes.execute_run(
        ["agents", "latency"],
        {"target_ms": 1500},
        creds=object(),
        probes=_SlowLatencyProbes(),
    )
    assert out["checks"]["latency"]["ok"] is True
    assert out["verdict"] == "WARN"
