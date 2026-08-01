"""The whole path, driven through the CLI and read back off disk.

Every other test in this area exercises one seam. This one runs the commands an
operator runs, against a throwaway database, and then OPENS the two files that
get handed over. A seam that works in isolation and a report that carries the
result are different claims, and only the second one is the deliverable.

What it pins:

* the payload carries a capacity block and an appendix, both of which were
  accepted arguments that nothing passed until recently;
* the file states a verdict, rather than the verdict living only in the
  operator's terminal scrollback;
* a FAILING gate exits non-zero AND still leaves the evidence on disk;
* provenance stays synthetic and the not-a-deliverable stamp stays first, which
  is what stops a fixture-built file being mistaken for a measurement.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from voicegateway.cli._app import app

runner = CliRunner()

# The fixture run's own window, from summary.json: 2026-07-31T18:00Z to 19:00Z.
WINDOW_START_MS = 1_785_520_800_000

APPENDIX = {
    "commands": [
        {
            "label": "ramp step",
            "detail": "gossip sipp -l 200 -r 3.2258 -pause_ms 60000 -trace_stat",
            "citation": "runbook.md:75",
        }
    ],
    "flags": [
        {
            "label": "-l",
            "detail": "Max concurrent calls. Defaults to 1.",
            "citation": "runbook.md:53",
        }
    ],
}


@pytest.fixture
def run(tmp_path: Path, monkeypatch) -> dict:
    """Import the fixtures into a throwaway database under tmp_path.

    Never the repository's voicegw.db: VOICEGW_DB_PATH is cleared so a leaked
    environment variable cannot redirect the write, and db_path is explicit.
    """
    monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)
    fixtures = Path(__file__).resolve().parents[4] / ".agents" / "fixtures"
    if not (fixtures / "summary.json").is_file():
        pytest.skip("fixtures are not present in this checkout")

    run_dir = tmp_path / "ramp-500"
    run_dir.mkdir()
    for name in ("summary.json", "gossipper_stats.csv"):
        (run_dir / name).write_bytes((fixtures / name).read_bytes())

    db = tmp_path / "throwaway.db"
    config = tmp_path / "voicegw.yaml"
    config.write_text(
        yaml.dump({"cost_tracking": {"enabled": True, "db_path": str(db)}})
    )
    appendix = tmp_path / "appendix.json"
    appendix.write_text(json.dumps(APPENDIX))

    result = runner.invoke(
        app, ["loadtest", "import", str(run_dir), "--config", str(config)]
    )
    assert result.exit_code == 0, result.output
    return {
        "config": str(config),
        "appendix": str(appendix),
        "out": tmp_path / "out",
        "db": db,
    }


def _report(run: dict, *extra: str):
    return runner.invoke(
        app,
        [
            "loadtest",
            "report",
            "ramp-500",
            "--config",
            run["config"],
            "--out",
            str(run["out"]),
            "--appendix",
            run["appendix"],
            *extra,
        ],
    )


def _artifacts(run: dict) -> tuple[dict, str]:
    """Read the two files back off disk, as a recipient would."""
    written = list(run["out"].iterdir())
    payload = json.loads(next(p for p in written if p.suffix == ".json").read_text())
    html = next(p for p in written if p.suffix == ".html").read_text()
    return payload, html


# --------------------------------------------------------------------------
# The artifacts carry what the report is owed
# --------------------------------------------------------------------------


def test_the_payload_carries_capacity_and_appendix(run: dict) -> None:
    """Both were accepted arguments that nothing supplied."""
    _report(run)
    payload, _ = _artifacts(run)

    assert payload["capacity"] is not None
    # The fixtures cannot yield a figure, and the block says why rather than
    # being absent. A missing section reads as one nobody needed.
    assert payload["capacity"]["reason"]
    assert payload["appendix"] is not None
    assert payload["appendix"]["flags"][0]["citation"] == "runbook.md:53"


def test_the_file_states_a_verdict(run: dict) -> None:
    """It used to reach the terminal and never the artifact."""
    _report(run)
    payload, html = _artifacts(run)
    assert payload["verdict"]["status"]
    assert 'class="verdict' in html
    assert payload["verdict"]["status"] in html


def test_the_gates_travel_with_the_numbers(run: dict) -> None:
    _report(run)
    payload, _ = _artifacts(run)
    assert payload["gates_recorded"] is True
    assert {g["gate"] for g in payload["gates"]} >= {
        "call_establishment",
        "node_cpu",
        "node_memory",
        "resource_headroom",
    }


# --------------------------------------------------------------------------
# Provenance, which is what stops any of this being mistaken for measurement
# --------------------------------------------------------------------------


def test_provenance_stays_synthetic_and_the_stamp_stays_first(run: dict) -> None:
    _report(run)
    payload, html = _artifacts(run)
    assert payload["data_provenance"] == "synthetic"
    body = html[html.index("<body>") + len("<body>") :]
    assert body.lstrip().startswith('<div class="stamp">')
    assert "SYNTHETIC DATA: NOT A DELIVERABLE" in html
    # And the stamp precedes the verdict, so a reader meets it first.
    assert body.index("stamp") < body.index('class="verdict')


def test_the_delivered_file_reaches_for_nothing(run: dict) -> None:
    _report(run)
    _, html = _artifacts(run)
    lowered = html.lower()
    for marker in (
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
    ):
        assert marker not in lowered, f"the report reaches for {marker!r}"


# --------------------------------------------------------------------------
# A failing gate
# --------------------------------------------------------------------------


def _seed_breaching_cpu(db: Path) -> None:
    """Scrape samples inside the fixture's window showing 80% CPU.

    cpu_seconds_total accrues at the core count and the idle counter at what is
    left, so 4.0/s total against 0.8/s idle is 80% busy: over the 70% ceiling
    and therefore a FAIL rather than an UNKNOWN.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from voicegateway.repository.node_samples_repository import (
        NodeSampleInput,
        insert_samples,
    )

    async def _write() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
        total, idle = 1000.0, 800.0
        rows = []
        for i in range(6):
            rows.append(
                NodeSampleInput(
                    node="sfu-1",
                    source="node_exporter",
                    at_ms=WINDOW_START_MS + i * 600_000,
                    outcome="ok",
                    values={
                        "cpu_seconds_total": total,
                        "cpu_idle_seconds_total": idle,
                        "memory_total_bytes": 16_000_000_000,
                        "memory_available_bytes": 8_000_000_000,
                    },
                )
            )
            total += 4.0 * 600.0
            idle += 0.8 * 600.0
        async with AsyncSession(engine) as session:
            await insert_samples(session, rows)
            await session.commit()
        await engine.dispose()

    asyncio.run(_write())


def test_a_breaching_gate_exits_non_zero_and_still_writes_the_evidence(
    run: dict,
) -> None:
    """The property that matters most on a bad run.

    A failing run is exactly the one whose report somebody needs, so the exit
    code must be non-zero AND both files must be on disk explaining why.
    """
    _seed_breaching_cpu(run["db"])
    # Re-import so the correlation runs against the samples just seeded.
    fixtures_dir = Path(run["config"]).parent / "ramp-500"
    assert (
        runner.invoke(
            app, ["loadtest", "import", str(fixtures_dir), "--config", run["config"]]
        ).exit_code
        == 0
    )

    result = _report(run)
    payload, html = _artifacts(run)

    assert payload["verdict"]["status"] == "FAIL"
    assert result.exit_code != 0
    # The evidence survived the failure.
    assert payload["gates"]
    assert "FAIL" in html
    cpu = [g for g in payload["gates"] if g["gate"] == "node_cpu"]
    assert [g["status"] for g in cpu] == ["FAIL"]
    assert payload["tests"][0]["peak_cpu_utilisation"] == pytest.approx(0.80)
