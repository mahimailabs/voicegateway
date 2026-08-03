from __future__ import annotations

import asyncio
from types import SimpleNamespace

from voicegateway.livekit_diag import gates, run_report
from voicegateway.livekit_diag import service as probes
from voicegateway.livekit_diag.config import LiveKitCreds
from voicegateway.livekit_diag.latency import LatencyResult, summarize
from voicegateway.livekit_diag.report import render_latency
from voicegateway.livekit_diag.resources import ResourceReport
from voicegateway.livekit_diag.sfu import RampStep, find_knee


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


# ---------------------------------------------------------------------------
# RealProbes payloads: what the dashboard can actually render.
#
# These pin the three things the probes measured and used to drop on the floor:
# the heartbeat roster (idle workers are invisible to LiveKit's server API), the
# per-step connection quality, and the prober's own resource report (without
# which a knee cannot be attributed to the SFU rather than to this host).
# ---------------------------------------------------------------------------

_CREDS = LiveKitCreds("wss://x", "k", "s")


class _FakeAdmin:
    def __init__(self, creds) -> None:
        self.url = ""
        self.closed = False

    async def list_agents(self):
        return ["agent-a"]

    async def aclose(self) -> None:
        self.closed = True


class _FakeSfuProbe:
    """Returns real RampStep / ResourceReport objects, so the shapes stay honest."""

    def __init__(self, admin, client_factory, monitor) -> None:
        pass

    async def baseline(self, room, seconds: float = 10.0):
        return RampStep(2, 11.4, 0.0, "Excellent")

    async def ramp(self, room, steps, duration, target_rtt_ms, max_loss, **kwargs):
        return (
            [
                RampStep(2, 12.0, 0.0, "Excellent", 2),
                RampStep(10, 61.0, 0.0, "Poor", 10),
            ],
            ResourceReport(
                cpu_peak=91.2,
                mem_peak_mb=812.5,
                net_kbps_up=4200.0,
                saturated=True,
                per_client={"cpu_pct": 3.6, "kbps_up": 168.0},
                sustainable_n=23,
            ),
        )


def _fake_diag(**overrides):
    ns = {
        "LiveKitAdmin": _FakeAdmin,
        "agents_json": lambda rows: [{"agent_name": r} for r in rows],
        "fetch_roster": None,
        "SfuProbe": _FakeSfuProbe,
        "SyntheticClient": lambda url, token: object(),
        "ResourceMonitor": lambda: object(),
        "find_knee": find_knee,
    }
    ns.update(overrides)
    return SimpleNamespace(**ns)


async def test_agents_check_returns_none_roster_without_a_collector(monkeypatch):
    """No collector configured is None, never an empty list.

    [] would claim "the collector is up and nobody has registered"; None is the
    honest "nobody asked", which is what lets the UI print how to enable it.
    """
    monkeypatch.delenv("VOICEGW_COLLECTOR_URL", raising=False)
    monkeypatch.setattr(probes, "_diag_cache", _fake_diag())
    out = await probes.RealProbes().agents(_CREDS)
    assert out["agents"] == [{"agent_name": "agent-a"}]
    assert out["roster"] is None


async def test_agents_check_includes_the_heartbeat_roster(monkeypatch):
    seen: list[tuple[str, str | None]] = []

    async def _fetch(collector, api_key):
        seen.append((collector, api_key))
        return [{"agent_name": "idle-worker", "status": "idle"}]

    monkeypatch.setenv("VOICEGW_COLLECTOR_URL", "https://collector.test")
    monkeypatch.setenv("VOICEGW_API_KEY", "vk_secret")
    monkeypatch.setattr(probes, "_diag_cache", _fake_diag(fetch_roster=_fetch))

    out = await probes.RealProbes().agents(_CREDS)
    assert seen == [("https://collector.test", "vk_secret")]
    assert out["roster"] == [{"agent_name": "idle-worker", "status": "idle"}]
    # The in-room view is unaffected by the enrichment.
    assert out["agents"] == [{"agent_name": "agent-a"}]


async def test_agents_check_survives_a_hanging_collector(monkeypatch):
    """A collector that never answers costs the roster, not the whole check."""

    async def _hang(collector, api_key):
        await asyncio.sleep(10)
        return []

    monkeypatch.setenv("VOICEGW_COLLECTOR_URL", "https://collector.test")
    monkeypatch.setattr(probes, "_ROSTER_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(probes, "_diag_cache", _fake_diag(fetch_roster=_hang))

    out = await probes.RealProbes().agents(_CREDS)
    assert out["roster"] == []
    assert out["agents"] == [{"agent_name": "agent-a"}]


async def test_sfu_load_payload_carries_quality_resource_and_threshold(monkeypatch):
    monkeypatch.setattr(probes, "_diag_cache", _fake_diag())
    out = await probes.RealProbes().sfu(_CREDS, True, probes.clamp_config({}))

    # Per-step quality is the only real connection signal (loss is not measured).
    assert [s["quality"] for s in out["ramp"]] == ["Excellent", "Poor"]
    # 10 clients breached 50ms, so the last healthy tier is 2.
    assert out["knee"] == 2
    # The threshold the knee was computed against must travel with it: without it
    # a knee of None is ambiguous (nothing breached vs the FIRST tier breached).
    assert out["target_rtt_ms"] == probes._TARGET_RTT_MS
    assert out["resource"]["saturated"] is True
    assert out["resource"]["cpu_peak"] == 91.2
    assert out["resource"]["per_client"]["cpu_pct"] == 3.6
    assert out["resource"]["sustainable_n"] == 23


async def test_sfu_baseline_only_has_no_ramp_and_no_resource(monkeypatch):
    monkeypatch.setattr(probes, "_diag_cache", _fake_diag())
    out = await probes.RealProbes().sfu(_CREDS, False, probes.clamp_config({}))
    assert out["ramp"] == []
    assert out["knee"] is None
    assert out["resource"] is None
    # _FakeSfuProbe builds its baseline RampStep without a sample count, so the
    # dataclass default of 0 travels: a hand-built step is not credited with
    # evidence it does not carry, and rtt_stat says so rather than implying the
    # 11.4 came from somewhere.
    assert out["baseline"] == {
        "rtt_ms": 11.4,
        "loss_pct": 0.0,
        "quality": "Excellent",
        "samples": 0,
        "rtt_stat": "not_measured",
    }


def test_resource_json_reports_unsampled_metrics_as_none():
    """An unsampled metric is None, not 0.

    ResourceReport defaults every unsampled metric to 0.0 (and psutil's first
    cpu_percent call always returns 0.0), so a 0 would render as "this host was
    idle under load" - the exact opposite of what an unmeasured sample means.
    """
    out = probes._resource_json(
        ResourceReport(
            cpu_peak=0.0,
            mem_peak_mb=0.0,
            net_kbps_up=0.0,
            saturated=False,
            per_client={"cpu_pct": 0.0, "kbps_up": 0.0},
            sustainable_n=None,
        )
    )
    assert out is not None
    assert out["cpu_peak"] is None
    assert out["net_kbps_up"] is None
    assert out["mem_peak_mb"] is None
    # Saturation is derived from the CPU sample: with no sample it is unknown,
    # not False (False would be a clean bill of health nobody measured).
    assert out["saturated"] is None
    assert out["per_client"] == {"cpu_pct": None, "kbps_up": None}
    assert out["sustainable_n"] is None


def test_resource_json_is_none_without_a_report():
    assert probes._resource_json(None) is None


# ---------------------------------------------------------------------------
# The latency payload says WHY an agent answered nothing.
#
# RealProbes.latency used to drop LatencyResult.error, so a run that knew
# "dispatched to 'reception' but no worker joined within 8s" reached the
# dashboard as a bare "no reply" while the CLI printed the reason from the same
# object. These pin the two states apart: a no-reply WITH a recorded reason, and
# a no-reply WITHOUT one, which must stay an absence rather than become an empty
# explanation.
# ---------------------------------------------------------------------------

# Verbatim from ProbeRunner._await_agent's explicit-dispatch failure.
_NO_WORKER = (
    "dispatched to 'reception' but no worker joined within 8s: that name is how "
    "the worker registered (register_worker / @server.rtc_session agent_name); "
    "check a worker with that name is running"
)

# Copied from `DiagLatencyAgent` in src/dashboard/frontend/src/lib/types.ts, in
# declaration order. The Latency and Errors tabs are written against exactly
# these names, and a renamed or dropped key still compiles over there: it renders
# a permanently blank reason instead. The mismatch fails here instead.
_LATENCY_AGENT_FIELDS = ["agent", "stats", "components", "error"]

_PROBED = ("support-voice", "reception", "checkout-voice")


class _FakeLatencyAdmin:
    """list_agents returns rows, not names: RealProbes reads ``.agent_name``."""

    def __init__(self, creds) -> None:
        self.url = ""
        self.closed = False

    async def list_agents(self):
        return [SimpleNamespace(agent_name=name) for name in _PROBED]

    async def aclose(self) -> None:
        self.closed = True


def _runner_returning(results: dict[str, LatencyResult]):
    """A ProbeRunner stand-in that hands back one canned result per agent."""

    class _FakeRunner:
        def __init__(self, admin, client_factory, utterance, reader) -> None:
            pass

        async def probe(
            self, agent, trials, warmup, room_name, metadata, *, dispatch=True
        ) -> LatencyResult:
            return results[agent]

    return _FakeRunner


def _results() -> dict[str, LatencyResult]:
    """One agent per outcome: answered, failed with a reason, failed silently."""
    return {
        "support-voice": LatencyResult(
            agent="support-voice",
            e2e_samples=[0.9, 1.1],
            components={"llm_ttft": 0.4, "tts": 0.2},
        ),
        # The probe raised, so the reason is on the result.
        "reception": LatencyResult(agent="reception", error=_NO_WORKER),
        # The worker joined and never replied: wait_reply returned None and
        # nothing raised, so no reason exists to report.
        "checkout-voice": LatencyResult(agent="checkout-voice"),
    }


def _install_fake_diag(monkeypatch, results: dict[str, LatencyResult]) -> None:
    monkeypatch.setattr(
        probes,
        "_diag_cache",
        _fake_diag(
            LiveKitAdmin=_FakeLatencyAdmin,
            ProbeRunner=_runner_returning(results),
            UtteranceSource=lambda path: object(),
            ComponentReader=lambda *args, **kwargs: object(),
            summarize=summarize,
            # Only _utterance_path reads this, and the fake UtteranceSource
            # never opens what it is handed.
            pkg=SimpleNamespace(__file__="/nonexistent/livekit_diag/__init__.py"),
        ),
    )


async def _latency_payload(monkeypatch) -> dict:
    _install_fake_diag(monkeypatch, _results())
    return await probes.RealProbes().latency(_CREDS, probes.clamp_config({}))


async def test_latency_payload_carries_the_reason_the_probe_recorded(monkeypatch):
    out = await _latency_payload(monkeypatch)
    entries = {a["agent"]: a for a in out["agents"]}

    assert entries["reception"]["stats"]["trials"] == 0
    # Verbatim: the dashboard needs the provider's / server's own words, and a
    # summary written here would be a second vocabulary for one failure.
    assert entries["reception"]["error"] == _NO_WORKER


async def test_an_agent_with_no_recorded_reason_reads_as_an_absence(monkeypatch):
    """No reply and nobody said why is a real state, and not an empty reason.

    None is the whole point: an empty string would render as an explanation that
    explains nothing, which reads as "the probe answered the question" when it
    did not.
    """
    out = await _latency_payload(monkeypatch)
    entries = {a["agent"]: a for a in out["agents"]}

    assert entries["checkout-voice"]["stats"]["trials"] == 0
    assert entries["checkout-voice"]["error"] is None
    # An agent that DID answer has nothing to explain either, and is not given
    # an invented reason for having succeeded.
    assert entries["support-voice"]["stats"]["trials"] == 2
    assert entries["support-voice"]["error"] is None


async def test_latency_agent_fields_match_the_frontend_type(monkeypatch):
    """Field for field against types.ts: no invented, renamed or dropped key."""
    out = await _latency_payload(monkeypatch)
    assert [a["agent"] for a in out["agents"]] == list(_PROBED)
    for entry in out["agents"]:
        assert list(entry) == _LATENCY_AGENT_FIELDS


async def test_the_gate_reads_the_reason_and_says_what_the_cli_says(monkeypatch):
    """One failure, one sentence, both surfaces.

    ``gates._latency_gate`` has always read ``entry["error"]`` and found nothing
    there on a dashboard run. Now that the payload carries it, the gate detail a
    run records must name the same cause the CLI's ``render_latency`` prints for
    the same probe result.
    """
    results = _results()
    _install_fake_diag(monkeypatch, results)

    # Clamped, as the endpoint always hands it over: RealProbes.latency reads
    # config["trials"], which only clamp_config guarantees.
    out = await probes.execute_run(
        ["latency"],
        probes.clamp_config({"target_ms": 1500}),
        creds=_CREDS,
        probes=probes.RealProbes(),
    )
    details = [g["detail"] for g in out["gates"]]
    cli = render_latency(list(results.values()), 1500.0, summarize)

    # The agent that failed with a reason: the reason itself, on both surfaces.
    assert any(_NO_WORKER in d for d in details)
    assert f"no successful probe ({_NO_WORKER})" in cli
    # The agent that failed without one: the same "no reply" fallback on both,
    # rather than a blank parenthesis or a second phrase for the same absence.
    assert any("no successful probe" in d and "(no reply)" in d for d in details)
    assert "no successful probe (no reply)" in cli


async def test_the_run_report_still_renders_after_the_payload_change(monkeypatch):
    """The report reads the keys it knows and ignores the new one.

    ``run_report`` is shared byte for byte by the dashboard's download and
    ``voicegw livekit report``, so a payload change that broke it would break
    both. Nothing it renders moves: the agents that measured nothing still read
    as measured-nothing, and no number is invented for them.
    """
    payload = await _latency_payload(monkeypatch)
    run = run_report.RunRecord(
        run_id="run_n2",
        checks=["latency"],
        config={"target_ms": 1500},
        status="done",
        results={"checks": {"latency": {"ok": True, "result": payload}}},
        verdict="UNKNOWN",
        created_at="2026-07-31T12:00:00+00:00",
    )

    finding = run_report.build_payload(run, livekit_url="wss://x")["findings"][
        "latency"
    ]
    assert [a["agent"] for a in finding["agents"]] == list(_PROBED)
    assert [a["measured"] for a in finding["agents"]] == [True, False, False]
    # Unmeasured stays null, never zero, for both no-reply agents.
    assert [a["avg_ms"] for a in finding["agents"][1:]] == [None, None]

    document = run_report.render_html(
        run_report.build_payload(run, livekit_url="wss://x")
    )
    assert "reception" in document and "checkout-voice" in document
    assert "no trial produced a reply" in document


# What rtt_ms IS travels with it: a tier that measured nothing says so
# ---------------------------------------------------------------------------


class _MixedSfuProbe:
    """A ramp with one measured tier and one where every ping timed out."""

    def __init__(self, admin, client_factory, monitor) -> None:
        pass

    async def baseline(self, room, seconds: float = 10.0):
        return RampStep(2, 11.4, 0.0, "Excellent", 2)

    async def ramp(self, room, steps, duration, target_rtt_ms, max_loss, **kwargs):
        return (
            [
                RampStep(2, 12.0, 0.0, "Excellent", 2),
                # 10 clients, not one pong back: rtt_ms 0.0 is the placeholder
                # mean of an empty list, and quality has nothing to read.
                RampStep(10, 0.0, 0.0, "Unknown", 0),
            ],
            None,
        )


class _AllTimedOutSfuProbe:
    """Every tier timed out: the ramp that used to report a clean PASS."""

    def __init__(self, admin, client_factory, monitor) -> None:
        pass

    async def baseline(self, room, seconds: float = 10.0):
        return RampStep(2, 0.0, 0.0, "Unknown", 0)

    async def ramp(self, room, steps, duration, target_rtt_ms, max_loss, **kwargs):
        return (
            [RampStep(2, 0.0, 0.0, "Unknown", 0), RampStep(10, 0.0, 0.0, "Unknown", 0)],
            None,
        )


async def test_ramp_steps_publish_the_sample_count_behind_rtt(monkeypatch):
    """0.0ms and 12.0ms are both floats; only ``samples`` says which is a reading."""
    import json

    monkeypatch.setattr(probes, "_diag_cache", _fake_diag(SfuProbe=_MixedSfuProbe))
    out = await probes.RealProbes().sfu(_CREDS, True, probes.clamp_config({}))

    assert [s["samples"] for s in out["ramp"]] == [2, 0]
    assert [s["rtt_stat"] for s in out["ramp"]] == ["mean_of_n", "not_measured"]
    # Runs are persisted and re-read, so the honesty has to survive the JSON.
    assert json.loads(json.dumps(out))["ramp"] == out["ramp"]


async def test_a_measured_smallest_tier_still_passes_through_the_payload(monkeypatch):
    monkeypatch.setattr(probes, "_diag_cache", _fake_diag(SfuProbe=_MixedSfuProbe))
    out = await probes.RealProbes().sfu(_CREDS, True, probes.clamp_config({}))

    gate = gates.sfu_capacity_gate(out["ramp"], out["target_rtt_ms"], out["resource"])
    assert gate.status == gates.PASS  # 12.0ms of 2 real round-trips, budget 50ms


async def test_a_ramp_that_measured_nothing_does_not_pass_the_capacity_gate(
    monkeypatch,
):
    """End to end: the probe's own payload, fed to the gate that reads it."""
    monkeypatch.setattr(
        probes, "_diag_cache", _fake_diag(SfuProbe=_AllTimedOutSfuProbe)
    )
    out = await probes.RealProbes().sfu(_CREDS, True, probes.clamp_config({}))

    assert [s["rtt_stat"] for s in out["ramp"]] == ["not_measured", "not_measured"]
    gate = gates.sfu_capacity_gate(out["ramp"], out["target_rtt_ms"], out["resource"])
    assert gate.status == gates.UNKNOWN
    assert gates.exit_code(gates.verdict([gate])) == 1


# ---------------------------------------------------------------------------
# The same, for the BASELINE: its quality and its rtt are separate readings
# ---------------------------------------------------------------------------


class _SilentPingSfuProbe:
    """The connection came up; not one ping came back.

    ``quality`` is the SDK's own peer-connection metric, so it reads
    ``Excellent``; ``rtt_ms`` is a mean over round trips, so it is the 0.0 of an
    empty list. This is the pair that used to satisfy ``sfu_quality_gate``.
    """

    def __init__(self, admin, client_factory, monitor) -> None:
        pass

    async def baseline(self, room, seconds: float = 10.0):
        return RampStep(2, 0.0, 0.0, "Excellent", 0)

    async def ramp(self, room, steps, duration, target_rtt_ms, max_loss, **kwargs):
        return [], None


async def test_the_baseline_publishes_the_sample_count_behind_its_rtt(monkeypatch):
    """Without it, 'Excellent, rtt 0.0ms' is indistinguishable from a fast SFU."""
    import json

    monkeypatch.setattr(probes, "_diag_cache", _fake_diag(SfuProbe=_MixedSfuProbe))
    out = await probes.RealProbes().sfu(_CREDS, False, probes.clamp_config({}))

    assert out["baseline"]["samples"] == 2
    assert out["baseline"]["rtt_stat"] == "mean_of_n"
    # Runs are persisted and re-read, so the honesty has to survive the JSON.
    assert json.loads(json.dumps(out))["baseline"] == out["baseline"]


async def test_a_measured_baseline_still_passes_the_quality_gate(monkeypatch):
    monkeypatch.setattr(probes, "_diag_cache", _fake_diag(SfuProbe=_MixedSfuProbe))
    out = await probes.RealProbes().sfu(_CREDS, False, probes.clamp_config({}))

    gate = gates.sfu_quality_gate(out["baseline"])
    assert gate.status == gates.PASS  # Excellent over 2 real round-trips
    assert "rtt 11.4ms" in gate.detail


async def test_an_excellent_baseline_that_measured_nothing_does_not_pass(monkeypatch):
    """End to end: the probe's own payload, fed to the gate that reads it."""
    monkeypatch.setattr(probes, "_diag_cache", _fake_diag(SfuProbe=_SilentPingSfuProbe))
    out = await probes.RealProbes().sfu(_CREDS, False, probes.clamp_config({}))

    assert out["baseline"]["quality"] == "Excellent"  # the SDK really said this
    assert out["baseline"]["rtt_stat"] == "not_measured"
    gate = gates.sfu_quality_gate(out["baseline"])
    assert gate.status == gates.UNKNOWN
    assert gate.status != gates.PASS
    assert "rtt 0.0ms" not in gate.detail
    assert gates.exit_code(gates.verdict([gate])) == 1
