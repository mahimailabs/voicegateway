"""``voicegw livekit report``: the run report as a file, without the dashboard.

The command exists so CI can collect the artifact on a host that never runs the
web UI. That is only worth anything if it is the SAME artifact, so the property
these tests hold is byte equality against the dashboard endpoint for the same
run -- not "looks similar". A second renderer would drift the first time either
side was edited, and nobody would notice until a client read the wrong file.

The rest is what the report promises about itself:

* the HTML is genuinely SELF-CONTAINED (no script, link, img, font, url() or any
  other subresource), asserted by scanning the bytes the CLI wrote;
* a FAILED run (``results`` is NULL) still renders, and renders as a run that
  measured nothing rather than as a run that measured zeros;
* ``schema_version`` is still 1: moving the renderer changed no payload.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import yaml
from typer.testing import CliRunner

from voicegateway.cli._app import app
from voicegateway.core.gateway import Gateway
from voicegateway.livekit_diag import gates, run_report
from voicegateway.server.api.dashboard import diagnostics

runner = CliRunner()

# Frozen so two exports of one run differ in nothing at all. generated_at is the
# only value in the payload that is not a property of the run.
_FROZEN = "2026-07-31T12:00:00+00:00"

_RESULTS: dict[str, Any] = {
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
            "detail": "reception: no successful probe (no reply)",
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
                "baseline": {"rtt_ms": 18.4, "loss_pct": 0.0, "quality": "Excellent"},
                "ramp": [],
                "knee": None,
                "target_rtt_ms": 50.0,
                "resource": None,
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
}


def _strip(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _seed(tmp_path, monkeypatch, **overrides: Any) -> tuple[str, str]:
    """A config + a stored diagnostics run. Returns (config path, run id).

    The credentials go in the environment so the CLI and the endpoint resolve the
    identical LiveKit URL: the report names the server the EXPORTING host
    resolves, because no run record has ever carried the one it probed.
    """
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "diag-report-cli.db"))
    monkeypatch.setenv("LIVEKIT_URL", "wss://livekit.example")
    monkeypatch.setenv("LIVEKIT_API_KEY", "k")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "s")
    cfg = tmp_path / "voicegw.yaml"
    cfg.write_text(
        yaml.dump(
            {
                "providers": {"openai": {"api_key": "x"}},
                "models": {"stt": {}, "llm": {}, "tts": {}},
                "projects": {},
                "cost_tracking": {"enabled": True},
            }
        )
    )
    row: dict[str, Any] = {
        "run_id": "run" + "a" * 13,
        "checks": ["agents", "latency", "sfu"],
        "config": {"target_ms": 1500.0, "ramp": [2, 10], "trials": 3},
        "status": "done",
        "results": _RESULTS,
        "verdict": gates.UNKNOWN,
        "error": None,
        "created_at": "2026-07-31T09:00:00+00:00",
        "started_at": "2026-07-31T09:00:01+00:00",
        "ended_at": "2026-07-31T09:01:37+00:00",
    }
    row.update(overrides)

    async def seed() -> None:
        gw = Gateway(config_path=str(cfg))
        await gw.storage.upsert_diagnostics_run(**row)

    asyncio.run(seed())
    return str(cfg), str(row["run_id"])


def _endpoint_html(cfg: str, run_id: str) -> bytes:
    """The exact bytes the dashboard's ``/report.html`` would send for this run.

    The endpoint coroutine is called directly rather than over ASGI: this is
    about what the endpoint renders, and going through the app would only add a
    transport that neither surface is being compared on.
    """

    async def call() -> bytes:
        gw = Gateway(config_path=cfg)
        response = await diagnostics.get_run_report_html(run_id, None, gw)
        return bytes(response.body)

    return asyncio.run(call())


# ---------------------------------------------------------------------------
# One renderer, two surfaces
# ---------------------------------------------------------------------------


def test_the_endpoint_renders_through_the_extracted_module() -> None:
    """The dashboard does not keep its own copy. Checked by identity."""
    assert diagnostics._render_report_html is run_report.render_html
    assert diagnostics._report_filename is run_report.report_filename
    assert diagnostics._run_from_row is run_report.run_from_row
    assert diagnostics._Run is run_report.RunRecord
    assert diagnostics.REPORT_SCHEMA_VERSION is run_report.REPORT_SCHEMA_VERSION


def test_cli_file_is_byte_identical_to_the_endpoint(tmp_path, monkeypatch) -> None:
    """The strongest form of "one renderer": the same run, the same bytes.

    The frozen clock is patched on ``run_report`` alone. If either surface still
    carried its own copy of the renderer, only one of them would be frozen and
    the timestamps would differ, so this fails for the right reason.
    """
    cfg, run_id = _seed(tmp_path, monkeypatch)
    monkeypatch.setattr(run_report, "_generated_at", lambda: _FROZEN)
    out = tmp_path / "report.html"

    result = runner.invoke(
        app, ["livekit", "report", "-c", cfg, "--run", run_id, "--out", str(out)]
    )
    assert result.exit_code == 0, result.output

    assert out.read_bytes() == _endpoint_html(cfg, run_id)
    assert _FROZEN in out.read_text()


def test_cli_writes_the_download_filename_by_default(tmp_path, monkeypatch) -> None:
    """No --out: the same name the dashboard's Content-Disposition offers."""
    cfg, run_id = _seed(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["livekit", "report", "-c", cfg, "--run", run_id])
    assert result.exit_code == 0, result.output

    written = tmp_path / f"voicegateway-diagnostics-{run_id}.html"
    assert written.exists()
    assert written.read_text().startswith("<!DOCTYPE html>")
    # The run's own verdict is reproduced on stdout, never recomputed here.
    assert gates.UNKNOWN in _strip(result.output)


# ---------------------------------------------------------------------------
# Self-contained
# ---------------------------------------------------------------------------

#: Every way an HTML file can reach for something it does not carry. A report
#: that needs the network is blank on the machine it is finally opened on, and
#: the CLI's whole purpose is producing a file that travels.
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
    "http://",
    "https://",
)


def test_written_file_reaches_for_nothing(tmp_path, monkeypatch) -> None:
    cfg, run_id = _seed(tmp_path, monkeypatch)
    out = tmp_path / "report.html"
    result = runner.invoke(
        app, ["livekit", "report", "-c", cfg, "--run", run_id, "--out", str(out)]
    )
    assert result.exit_code == 0, result.output

    document = out.read_text(encoding="utf-8").lower()
    assert document.startswith("<!doctype html>")
    assert "<style>" in document  # its CSS travels with it
    for marker in _EXTERNAL_MARKERS:
        assert marker not in document, f"report reaches outside itself: {marker!r}"


# ---------------------------------------------------------------------------
# A failed run
# ---------------------------------------------------------------------------


def test_failed_run_renders_and_fabricates_no_zeros(tmp_path, monkeypatch) -> None:
    """``results`` is NULL. The report says so; it does not invent numbers."""
    cfg, run_id = _seed(
        tmp_path,
        monkeypatch,
        status="failed",
        results=None,
        verdict=None,
        error="run timed out",
        ended_at="2026-07-31T09:06:01+00:00",
    )
    out = tmp_path / "failed.html"
    result = runner.invoke(
        app, ["livekit", "report", "-c", cfg, "--run", run_id, "--out", str(out)]
    )
    assert result.exit_code == 0, result.output

    document = out.read_text(encoding="utf-8")
    assert "NO VERDICT" in document
    assert "run timed out" in document
    # Requested but never recorded, and never requested, stay different answers.
    assert "This check was requested but recorded no result" in document
    assert "It is absent, not clean." in document
    # No measurement was recorded, so no number is rendered anywhere: not a
    # count of agents, not a latency, not a zero in a numeric cell.
    assert "agent(s) in" not in document
    assert '<td class="num">' not in document
    # No per-agent latency block at all: every label that carries a measured
    # value is absent, rather than present with a zero next to it.
    assert "Slowest reply" not in document
    assert "Average reply" not in document
    assert "Trials that answered" not in document
    # The only surviving "max of N" is the caveat explaining why a run with few
    # samples never prints a p95, which is prose about the report, not a figure.
    assert "as &quot;max of N&quot;" in document
    assert "max of 3" not in document
    # It is still the same document type, with its context and its caveats.
    assert "report schema v1" in document
    assert "What this report does not measure" in document


def test_failed_run_payload_is_null_not_zero(tmp_path, monkeypatch) -> None:
    """The JSON half of the same claim, read from the file the CLI wrote."""
    cfg, run_id = _seed(
        tmp_path,
        monkeypatch,
        status="failed",
        results=None,
        verdict=None,
        error="run timed out",
    )
    out = tmp_path / "failed.json"
    result = runner.invoke(
        app,
        ["livekit", "report", "-c", cfg, "--run", run_id, "--json", "--out", str(out)],
    )
    assert result.exit_code == 0, result.output

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["verdict"]["status"] is None
    assert payload["verdict"]["recorded"] is False
    assert payload["gates_recorded"] is False
    assert payload["gates"] is None
    assert payload["run"]["error"] == "run timed out"
    latency = payload["findings"]["latency"]
    assert latency["state"] == "no_result"
    assert latency["agents"] == []
    agents = payload["findings"]["agents"]
    assert agents["state"] == "no_result"
    # Never measured, so null: a count of 0 would read as an empty fleet.
    assert agents["in_room_count"] is None
    assert agents["roster_configured"] is None
    assert payload["findings"]["sfu"]["baseline_rtt_ms"] is None
    assert payload["findings"]["load"]["knee"] is None


# ---------------------------------------------------------------------------
# Payload contract
# ---------------------------------------------------------------------------


def test_schema_version_is_still_one(tmp_path, monkeypatch) -> None:
    """Extraction changed no payload, so the published version does not move."""
    assert run_report.REPORT_SCHEMA_VERSION == 1
    cfg, run_id = _seed(tmp_path, monkeypatch)
    out = tmp_path / "report.json"
    result = runner.invoke(
        app,
        ["livekit", "report", "-c", cfg, "--run", run_id, "--json", "--out", str(out)],
    )
    assert result.exit_code == 0, result.output

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["kind"] == "voicegateway.diagnostics.run_report"
    assert payload["target"]["livekit_url"] == "wss://livekit.example"
    assert payload["target"]["recorded_with_run"] is False
    # Three samples is not a percentile, on this surface either.
    support = payload["findings"]["latency"]["agents"][0]
    assert support["tail"]["statistic"] == "max_of_3"


def test_json_to_stdout_when_no_out_is_given(tmp_path, monkeypatch) -> None:
    cfg, run_id = _seed(tmp_path, monkeypatch)
    result = runner.invoke(
        app, ["livekit", "report", "-c", cfg, "--run", run_id, "--json"]
    )
    assert result.exit_code == 0, result.output
    assert "schema_version" in _strip(result.output)
    assert not list(tmp_path.glob("voicegateway-diagnostics-*.html"))


# ---------------------------------------------------------------------------
# When there is nothing to export
# ---------------------------------------------------------------------------


def test_unknown_run_id_exits_one(tmp_path, monkeypatch) -> None:
    cfg, _ = _seed(tmp_path, monkeypatch)
    result = runner.invoke(app, ["livekit", "report", "-c", cfg, "--run", "nope"])
    assert result.exit_code == 1
    assert "no diagnostics run" in _strip(result.output)


def test_no_recorded_run_exits_one_and_says_where_runs_come_from(
    tmp_path, monkeypatch
) -> None:
    """An empty store is not an empty report: there is nothing to export."""
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "empty.db"))
    cfg = tmp_path / "voicegw.yaml"
    cfg.write_text(
        yaml.dump(
            {
                "providers": {"openai": {"api_key": "x"}},
                "models": {"stt": {}, "llm": {}, "tts": {}},
                "projects": {},
                "cost_tracking": {"enabled": True},
            }
        )
    )
    result = runner.invoke(app, ["livekit", "report", "-c", str(cfg)])
    assert result.exit_code == 1
    out = _strip(result.output)
    assert "no diagnostics run is recorded on this host yet" in out
    assert "Diagnostics page" in out


def test_load_report_file_reaches_for_nothing() -> None:
    """Same scan as the diagnostics file, including absolute URLs anywhere.

    Extends the list above rather than starting a third. A load report is
    handed over and opened from disk, so an http:// anywhere in it is a hole.
    """
    from voicegateway.livekit_diag import run_report

    document = run_report.render_load_html(
        run_report.build_load_payload(
            run={"id": "ramp-500", "artifact_sha256": None},
            tests=[{"name": "ramp-500", "peak_concurrency": 492}],
            capacity={
                "calls_per_node": 150,
                "reason": "sustained under the CPU ceiling",
                "tiers": [
                    {
                        "target_concurrency": 500,
                        "nodes_for_load": 4,
                        "spare_nodes": 1,
                        "nodes": 5,
                    }
                ],
                "instance_type": {
                    "name": "c7i.2xlarge",
                    "role": "SIP",
                    "citation": "sizing-runbook.md:115",
                },
            },
        )
    )
    lowered = document.lower()
    for marker in _EXTERNAL_MARKERS:
        assert marker not in lowered, f"load report reaches for {marker!r}"
