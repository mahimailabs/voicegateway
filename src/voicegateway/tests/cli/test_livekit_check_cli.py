"""``voicegw livekit check``: what it prints and what it exits with.

The exit code is the product here: this command is meant to be run in CI, and a
0 from it is read as "the deployment is healthy". These tests pin the cases
where it used to say 0 without having measured anything.

No LiveKit server is involved: every collaborator the command reaches for is
replaced in the ``livekit_cli`` module namespace.
"""

from __future__ import annotations

import json
import re

import pytest
from typer.testing import CliRunner

from voicegateway.cli import livekit_cli
from voicegateway.cli._app import app
from voicegateway.livekit_diag.admin import AgentRow
from voicegateway.livekit_diag.latency import LatencyResult
from voicegateway.livekit_diag.sfu import RampStep

runner = CliRunner()


def _strip(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class _FakeAdmin:
    """Only what check_cmd touches: a url attribute, list_agents, aclose."""

    rows: list[AgentRow] = []

    def __init__(self, creds) -> None:
        self.url = ""

    async def list_agents(self) -> list[AgentRow]:
        return list(type(self).rows)

    async def aclose(self) -> None:
        return None


class _FakeRunner:
    """Returns a canned LatencyResult per probed agent."""

    results: dict[str, LatencyResult] = {}

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def probe(self, agent, trials, warmup, room_name, metadata, **kwargs):
        return type(self).results.get(agent, LatencyResult(agent=agent))


class _FakeSfuProbe:
    baseline_step = RampStep(2, 11.0, 0.0, "Excellent")

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def baseline(self, room, seconds: float = 10.0) -> RampStep:
        return type(self).baseline_step


@pytest.fixture(autouse=True)
def _stub_livekit(monkeypatch, tmp_path):
    monkeypatch.setenv("LIVEKIT_URL", "wss://fake")
    monkeypatch.setenv("LIVEKIT_API_KEY", "k")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "s")
    # A non-existent DB path keeps _component_reader storeless (no alembic
    # bootstrap just to read a split this test does not exercise).
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "absent.db"))
    monkeypatch.delenv("VOICEGW_COLLECTOR_URL", raising=False)
    monkeypatch.setattr(livekit_cli, "LiveKitAdmin", _FakeAdmin)
    monkeypatch.setattr(livekit_cli, "ProbeRunner", _FakeRunner)
    monkeypatch.setattr(livekit_cli, "SfuProbe", _FakeSfuProbe)
    monkeypatch.setattr(livekit_cli, "SyntheticClient", lambda *a, **k: object())
    monkeypatch.setattr(livekit_cli, "UtteranceSource", lambda *a, **k: object())
    monkeypatch.setattr(livekit_cli, "ResourceMonitor", lambda *a, **k: object())
    monkeypatch.setattr(livekit_cli, "_utterance_path", lambda: "probe.wav")
    _FakeAdmin.rows = []
    _FakeRunner.results = {}
    _FakeSfuProbe.baseline_step = RampStep(2, 11.0, 0.0, "Excellent")
    yield
    _FakeAdmin.rows = []
    _FakeRunner.results = {}


def _agent(name: str = "realty") -> AgentRow:
    return AgentRow(name, "room-1", name, "active", 1, 12.0)


def _healthy_fleet(samples: list[float]) -> None:
    _FakeAdmin.rows = [_agent()]
    _FakeRunner.results = {"realty": LatencyResult(agent="realty", e2e_samples=samples)}


def test_a_healthy_deployment_exits_zero():
    _healthy_fleet([0.8, 0.82])
    result = runner.invoke(app, ["livekit", "check"])
    assert result.exit_code == 0
    out = _strip(result.output)
    assert "VERDICT: PASS" in out
    assert "[PASS] agent_reply_latency" in out


def test_no_agent_in_any_room_no_longer_reports_a_clean_pass():
    """The headline behaviour change.

    Both old verdict implementations iterated an empty latency list, found
    nothing to complain about, and returned PASS. Nothing was measured, so
    nothing was demonstrated: it is UNKNOWN now, and it exits 1.
    """
    _FakeAdmin.rows = []
    result = runner.invoke(app, ["livekit", "check"])
    assert result.exit_code == 1
    out = _strip(result.output)
    assert "VERDICT: UNKNOWN" in out
    assert "[UNKNOWN] agent_reply_latency" in out
    assert "no agent was probed" in out


def test_an_agent_that_answered_nothing_exits_non_zero_and_says_why():
    _FakeAdmin.rows = [_agent()]
    _FakeRunner.results = {
        "realty": LatencyResult(agent="realty", error="no worker joined within 8s")
    }
    result = runner.invoke(app, ["livekit", "check"])
    assert result.exit_code == 1
    out = _strip(result.output)
    assert "VERDICT: UNKNOWN" in out
    assert "no worker joined within 8s" in out


def test_a_degraded_sfu_baseline_fails():
    _healthy_fleet([0.8, 0.82])
    _FakeSfuProbe.baseline_step = RampStep(2, 180.0, 0.0, "Poor")
    result = runner.invoke(app, ["livekit", "check"])
    assert result.exit_code == 1
    out = _strip(result.output)
    assert "VERDICT: FAIL" in out
    assert "[FAIL] sfu_connection_quality" in out


def test_strict_gates_the_slow_tail_and_names_the_metric_honestly():
    """M3's demoable: --strict fails CI naming the latency metric.

    The two probe turns average under the 1.5s target but one of them took
    2.4s. Default mode passes on the average; --strict fails on the tail and
    names it ``max_of_2`` -- never ``p95``, which two samples cannot support.
    """
    _healthy_fleet([0.5, 2.4])

    lenient = runner.invoke(app, ["livekit", "check"])
    assert lenient.exit_code == 0

    strict = runner.invoke(app, ["livekit", "check", "--strict"])
    assert strict.exit_code == 1
    out = _strip(strict.output)
    assert "VERDICT: WARN" in out
    assert "agent_reply_latency_max_of_2_ms" in out
    assert "p95" not in _strip(
        "\n".join(line for line in out.splitlines() if "agent_reply_latency" in line)
    )


def test_json_output_carries_the_gates_and_still_exits_non_zero():
    _FakeAdmin.rows = []
    result = runner.invoke(app, ["livekit", "check", "--json"])
    assert result.exit_code == 1
    payload = json.loads(_strip(result.output))
    assert payload["verdict"] == "UNKNOWN"
    statuses = {g["gate"]: g["status"] for g in payload["gates"]}
    assert statuses["agent_reply_latency"] == "UNKNOWN"
    # Every pre-existing key is still where it was.
    assert set(payload) >= {"agents", "latency", "sfu", "verdict"}


def test_a_target_the_deployment_misses_warns_and_exits_one():
    _healthy_fleet([2.0, 2.1])
    result = runner.invoke(app, ["livekit", "check", "--target-ms", "1000"])
    assert result.exit_code == 1
    out = _strip(result.output)
    assert "VERDICT: WARN" in out
    assert "agent_reply_latency_avg_ms" in out
