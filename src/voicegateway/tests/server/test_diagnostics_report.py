"""Tests for the diagnostics run report: ``/report`` (JSON) and ``/report.html``.

The report is a client deliverable, so the properties asserted here are the ones
a later edit breaks silently:

* the HTML is genuinely SELF-CONTAINED (no script, link, img, font, url() or any
  other subresource), because a report that needs a CDN is blank on the laptop it
  is opened on six months from now;
* nothing unmeasured renders as a number, and an UNKNOWN gate never renders as a
  pass;
* the tail statistic is named ``max_of_N`` and never ``p95`` below
  ``gates.MIN_PERCENTILE_SAMPLES`` samples;
* no packet-loss figure appears anywhere (``sfu.py`` hardcodes 0.0);
* the payload carries ``schema_version``, and both endpoints stay admin-gated.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.livekit_diag import gates
from voicegateway.livekit_diag.config import LiveKitCreds
from voicegateway.server.api.dashboard import diagnostics
from voicegateway.server.main import build_app

_FAKE_CREDS = LiveKitCreds("wss://livekit.example", "k", "s")


@pytest.fixture
def gateway(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "diag-report.db"))
    return Gateway(config_path=temp_config)


@pytest.fixture
async def client(gateway, monkeypatch):
    monkeypatch.setattr(diagnostics, "_resolve_creds", lambda: _FAKE_CREDS)
    app = build_app(gateway, enable_mcp_sse=False, enable_dashboard=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def _clear_registry():
    diagnostics._RUNS.clear()
    diagnostics._ORDER.clear()
    yield
    diagnostics._RUNS.clear()
    diagnostics._ORDER.clear()


def _register(run: diagnostics._Run) -> diagnostics._Run:
    """Put a hand-built run in the in-process working set the endpoints read."""
    diagnostics._RUNS[run.run_id] = run
    diagnostics._ORDER.append(run.run_id)
    return run


def _full_run() -> diagnostics._Run:
    """A run with one measured agent, one that answered nothing, and a ramp.

    Deliberately mixed: a report that only ever sees the happy payload cannot
    show whether it distinguishes "measured as zero" from "never measured".
    """
    return _register(
        diagnostics._Run(
            run_id="run" + "a" * 13,
            checks=["agents", "latency", "sfu", "sfu_load"],
            config={"target_ms": 1500.0, "ramp": [2, 10], "duration": 10.0, "trials": 3},
            status="done",
            verdict=gates.UNKNOWN,
            created_at="2026-07-31T09:00:00+00:00",
            started_at="2026-07-31T09:00:01+00:00",
            ended_at="2026-07-31T09:01:37+00:00",
            results={
                "verdict": gates.UNKNOWN,
                "gates": [
                    {
                        "gate": gates.LATENCY_GATE,
                        "status": gates.PASS,
                        "detail": "support: agent_reply_latency_avg_ms 912 is within",
                        "subject": "support",
                        "metric": "agent_reply_latency_avg_ms",
                        "value": 912.0,
                        "threshold": 1500.0,
                    },
                    {
                        "gate": gates.LATENCY_GATE,
                        "status": gates.UNKNOWN,
                        "detail": "reception: no successful probe, so reply latency"
                        " was not measured (no reply)",
                        "subject": "reception",
                        "metric": None,
                        "value": None,
                        "threshold": 1500.0,
                    },
                ],
                "checks": {
                    "agents": {
                        "ok": True,
                        "result": {
                            "agents": [
                                {
                                    "agent_name": "support",
                                    "room": "support-7f21",
                                    "identity": "agent-01",
                                    "state": "active",
                                    "humans": 1,
                                    "age_s": 132.0,
                                }
                            ],
                            "roster": None,
                        },
                    },
                    "sfu": {
                        "ok": True,
                        "result": {
                            "baseline": {
                                "rtt_ms": 18.4,
                                "loss_pct": 0.0,
                                "quality": "Excellent",
                            },
                            "ramp": [],
                            "knee": None,
                            "target_rtt_ms": 50.0,
                            "resource": None,
                        },
                    },
                    "sfu_load": {
                        "ok": True,
                        "result": {
                            "baseline": {
                                "rtt_ms": 19.1,
                                "loss_pct": 0.0,
                                "quality": "Excellent",
                            },
                            "ramp": [
                                {
                                    "clients": 2,
                                    "rtt_ms": 14.6,
                                    "loss_pct": 0.0,
                                    "quality": "Excellent",
                                },
                                {
                                    "clients": 10,
                                    "rtt_ms": 68.9,
                                    "loss_pct": 0.0,
                                    "quality": "Poor",
                                },
                            ],
                            "knee": 2,
                            "target_rtt_ms": 50.0,
                            "resource": {
                                "cpu_peak": None,
                                "mem_peak_mb": None,
                                "net_kbps_up": None,
                                "saturated": None,
                                "per_client": {"cpu_pct": None, "kbps_up": None},
                                "sustainable_n": None,
                            },
                        },
                    },
                    "latency": {
                        "ok": True,
                        "result": {
                            "agents": [
                                {
                                    "agent": "support",
                                    "stats": {
                                        "avg": 0.912,
                                        "p50": 0.884,
                                        "p95": 1.043,
                                        "min": 0.83,
                                        "max": 1.043,
                                        "trials": 3,
                                    },
                                    "components": {"stt": 0.168, "llm_ttft": 0.402},
                                },
                                {
                                    "agent": "reception",
                                    "stats": {
                                        "avg": 0.0,
                                        "p50": 0.0,
                                        "p95": 0.0,
                                        "min": 0.0,
                                        "max": 0.0,
                                        "trials": 0,
                                    },
                                    "components": None,
                                },
                            ]
                        },
                    },
                },
            },
        )
    )


# ---------------------------------------------------------------------------
# JSON export
# ---------------------------------------------------------------------------


async def test_report_json_carries_schema_version_and_kind(client):
    run = _full_run()
    resp = await client.get(f"/api/diagnostics/runs/{run.run_id}/report")
    assert resp.status_code == 200
    body = resp.json()
    assert body["schema_version"] == diagnostics.REPORT_SCHEMA_VERSION
    assert body["kind"] == "voicegateway.diagnostics.run_report"
    assert body["generator"]["tool"] == "voicegateway"
    # Context a number needs to survive being read out of band.
    assert body["run"]["run_id"] == run.run_id
    assert body["run"]["started_at"] == "2026-07-31T09:00:01+00:00"
    assert body["run"]["config"]["target_ms"] == 1500.0
    assert body["target"]["livekit_url"] == "wss://livekit.example"
    assert body["target"]["recorded_with_run"] is False
    assert body["generated_at"]


async def test_report_json_reads_the_stored_verdict_and_gates(client):
    """The report reports V4's verdict; it never re-derives one."""
    run = _full_run()
    body = (
        await client.get(f"/api/diagnostics/runs/{run.run_id}/report")
    ).json()
    assert body["verdict"]["status"] == gates.UNKNOWN
    assert body["verdict"]["decided_by"] == "voicegateway.livekit_diag.gates"
    assert "NOT a pass" in body["verdict"]["meaning"]
    assert body["gates_recorded"] is True
    assert [g["status"] for g in body["gates"]] == [gates.PASS, gates.UNKNOWN]


async def test_report_json_never_calls_three_samples_a_p95(client):
    run = _full_run()
    body = (
        await client.get(f"/api/diagnostics/runs/{run.run_id}/report")
    ).json()
    agents = {a["agent"]: a for a in body["findings"]["latency"]["agents"]}

    measured = agents["support"]
    assert measured["measured"] is True
    assert measured["tail"]["statistic"] == "max_of_3"
    assert measured["tail"]["label"] == "max of 3"
    assert measured["tail"]["value_ms"] == pytest.approx(1043.0)
    assert measured["avg_ms"] == pytest.approx(912.0)

    # An agent that answered nothing reports nulls, never summarize's zeros.
    silent = agents["reception"]
    assert silent["measured"] is False
    assert silent["trials_answered"] == 0
    assert silent["avg_ms"] is None
    assert silent["min_ms"] is None
    assert silent["tail"] is None
    assert silent["components_ms"] is None


async def test_report_json_reports_no_loss_figure(client):
    """sfu.py hardcodes loss_pct = 0.0; the report must not pass it on."""
    run = _full_run()
    resp = await client.get(f"/api/diagnostics/runs/{run.run_id}/report")
    body = resp.json()
    assert body["findings"]["sfu"]["packet_loss"] == "not_measured"
    # The hardcoded field never survives into the export, at any nesting level.
    assert "loss_pct" not in resp.text
    for step in body["findings"]["load"]["ramp"]:
        assert not [key for key in step if "loss" in key]


async def test_report_json_distinguishes_the_two_null_knees(client):
    """A knee of null means two opposite things; the report says which."""
    run = _full_run()
    body = (
        await client.get(f"/api/diagnostics/runs/{run.run_id}/report")
    ).json()
    load = body["findings"]["load"]
    assert load["knee"]["kind"] == "knee"
    assert load["knee"]["clients"] == 2
    assert load["knee"]["breach"]["clients"] == 10
    assert [s["within_budget"] for s in load["ramp"]] == [True, False]
    # Nothing was sampled on the prober, so saturation is unknown, not False.
    assert load["prober_host"]["saturated"] is None
    assert load["prober_host"]["cpu_peak_pct"] is None


async def test_report_json_separates_the_four_check_states(client):
    """not_requested / no_result / errored / ok are four different answers."""
    run = _register(
        diagnostics._Run(
            run_id="run" + "b" * 13,
            checks=["agents", "latency"],
            config={},
            status="done",
            verdict=gates.FAIL,
            created_at="2026-07-31T09:00:00+00:00",
            results={
                "verdict": gates.FAIL,
                "gates": [],
                "checks": {
                    "agents": {"ok": True, "result": {"agents": [], "roster": []}},
                    "latency": {"ok": False, "error": "check timed out"},
                },
            },
        )
    )
    findings = (
        await client.get(f"/api/diagnostics/runs/{run.run_id}/report")
    ).json()["findings"]
    assert findings["agents"]["state"] == "ok"
    assert findings["latency"]["state"] == "errored"
    assert findings["latency"]["error"] == "check timed out"
    # Never requested is not the same as requested-and-empty.
    assert findings["sfu"]["state"] == "not_requested"
    assert findings["load"]["state"] == "not_requested"
    # A configured collector reporting nothing is not "no collector".
    assert findings["agents"]["roster_configured"] is True
    assert findings["agents"]["roster"] == []


async def test_report_json_says_when_a_run_predates_gate_provenance(client):
    """A pre-gates run is reported as such, not re-judged at export time."""
    run = _register(
        diagnostics._Run(
            run_id="run" + "c" * 13,
            checks=["agents"],
            config={},
            status="done",
            verdict=gates.PASS,
            created_at="2026-07-31T09:00:00+00:00",
            results={
                "verdict": gates.PASS,
                "checks": {"agents": {"ok": True, "result": {"agents": []}}},
            },
        )
    )
    body = (
        await client.get(f"/api/diagnostics/runs/{run.run_id}/report")
    ).json()
    assert body["gates_recorded"] is False
    assert body["gates"] is None
    assert body["verdict"]["status"] == gates.PASS
    # No collector was asked, so the roster is "not configured", not "zero".
    assert body["findings"]["agents"]["roster_configured"] is False


async def test_report_404_for_unknown_run(client):
    assert (await client.get("/api/diagnostics/runs/nope/report")).status_code == 404
    assert (
        await client.get("/api/diagnostics/runs/nope/report.html")
    ).status_code == 404


# ---------------------------------------------------------------------------
# HTML export
# ---------------------------------------------------------------------------

#: Every way an HTML file can reach for something it does not carry. This list is
#: the whole point of the export: a report that needs the network is blank on the
#: machine it is finally opened on.
_EXTERNAL_MARKERS = (
    "<script",
    "<link",
    "<img",
    "<iframe",
    "<object",
    "<embed",
    "<svg",
    "@import",
    "url(",
    "src=",
    "srcset",
    "integrity=",
    "crossorigin",
    "//cdn",
    "fonts.googleapis",
)


async def test_report_html_is_self_contained(client):
    run = _full_run()
    resp = await client.get(f"/api/diagnostics/runs/{run.run_id}/report.html")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    document = resp.text

    assert document.startswith("<!DOCTYPE html>")
    assert "<style>" in document  # its CSS travels with it
    lowered = document.lower()
    for marker in _EXTERNAL_MARKERS:
        assert marker not in lowered, f"report reaches outside itself: {marker!r}"


async def test_report_html_downloads_with_a_sanitised_filename(client):
    run = _full_run()
    resp = await client.get(f"/api/diagnostics/runs/{run.run_id}/report.html")
    disposition = resp.headers["content-disposition"]
    assert disposition.startswith("attachment; ")
    assert f"voicegateway-diagnostics-{run.run_id}.html" in disposition
    # A run id from the path can never break out of the header.
    assert diagnostics._report_filename('x"; drop\n') == "voicegateway-diagnostics-xdrop.html"


async def test_report_html_escapes_what_it_does_not_own(client):
    """Agent names, rooms and provider errors are strings this server never wrote."""
    run = _register(
        diagnostics._Run(
            run_id="run" + "d" * 13,
            checks=["agents"],
            config={},
            status="done",
            verdict=gates.PASS,
            created_at="2026-07-31T09:00:00+00:00",
            results={
                "verdict": gates.PASS,
                "gates": [],
                "checks": {
                    "agents": {
                        "ok": True,
                        "result": {
                            "agents": [
                                {
                                    "agent_name": "<script>alert(1)</script>",
                                    "room": "r&d",
                                    "identity": None,
                                    "state": "active",
                                    "humans": 0,
                                    "age_s": None,
                                }
                            ],
                            "roster": None,
                        },
                    }
                },
            },
        )
    )
    document = (
        await client.get(f"/api/diagnostics/runs/{run.run_id}/report.html")
    ).text
    assert "<script" not in document.lower()
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in document
    assert "r&amp;d" in document


async def test_report_html_shows_unknown_as_unknown(client):
    """An UNKNOWN verdict must read as unknown, in words, not as a green tick."""
    run = _full_run()
    document = (
        await client.get(f"/api/diagnostics/runs/{run.run_id}/report.html")
    ).text
    assert gates.UNKNOWN in document
    assert "NOT a pass" in document
    # The UNKNOWN gate row states its status in text, not by colour alone.
    assert document.count(">UNKNOWN<") >= 2


async def test_report_html_never_prints_an_unmeasured_number(client):
    run = _full_run()
    document = (
        await client.get(f"/api/diagnostics/runs/{run.run_id}/report.html")
    ).text
    # The agent that answered nothing: no timings, an explicit sentence instead.
    assert "no trial produced a reply" in document
    assert "not measured rather than as a zero" in document
    # The tail is named for what it is. Every surviving "p95" is a caveat
    # explaining why there is no p95, never a label on a number.
    assert "max of 3" in document
    assert "p95" not in document.replace("a p95", "")
    # Loss is a labelled absence, not a figure.
    assert (
        "<tr><td>Packet loss</td>"
        '<td class="num"><span class="nm">not measured</span></td></tr>'
    ) in document
    # The prober host was not sampled at all.
    assert "Saturation is unknown for this run" in document


async def test_report_html_carries_its_own_context(client):
    """When it ran, against what, and what it does not measure."""
    run = _full_run()
    document = (
        await client.get(f"/api/diagnostics/runs/{run.run_id}/report.html")
    ).text
    assert run.run_id in document
    assert "2026-07-31T09:00:01+00:00" in document  # when it started
    assert "wss://livekit.example" in document  # what it was pointed at
    assert "1,500 ms" in document  # the target it was measured against
    assert "report schema v1" in document
    assert "What this report does not measure" in document


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


async def test_report_endpoints_require_admin_when_auth_enabled(gateway, monkeypatch):
    """Both new endpoints carry the router's admin gate.

    A report names the rooms, agents and provider errors of a deployment; it is
    at least as sensitive as the run it describes.
    """
    from voicegateway.core.auth import ADMIN_SCOPE, ApiKey

    monkeypatch.setattr(diagnostics, "_resolve_creds", lambda: _FAKE_CREDS)
    run = _full_run()
    app = build_app(gateway, enable_mcp_sse=False, enable_dashboard=True)
    app.state.api_keys = [
        ApiKey(token="admin-secret-token", name="ops", scopes=(ADMIN_SCOPE,))
    ]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        for path in (
            f"/api/diagnostics/runs/{run.run_id}/report",
            f"/api/diagnostics/runs/{run.run_id}/report.html",
        ):
            assert (await c.get(path)).status_code == 401
            allowed = await c.get(
                path, headers={"Authorization": "Bearer admin-secret-token"}
            )
            assert allowed.status_code == 200


# ---------------------------------------------------------------------------
# The two renderings agree
# ---------------------------------------------------------------------------


async def test_html_is_rendered_from_the_json_payload(client):
    """One payload, two renderings: they cannot describe different runs."""
    run = _full_run()
    payload: dict[str, Any] = (
        await client.get(f"/api/diagnostics/runs/{run.run_id}/report")
    ).json()
    document = (
        await client.get(f"/api/diagnostics/runs/{run.run_id}/report.html")
    ).text
    assert payload["verdict"]["status"] in document
    assert payload["findings"]["latency"]["agents"][0]["tail"]["label"] in document
    assert str(payload["schema_version"]) in document
