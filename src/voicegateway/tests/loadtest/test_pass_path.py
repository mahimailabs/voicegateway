"""The path where a run PASSES, which nothing had ever exercised.

Every real artifact in this repository is of a failed run. The report generator,
the gates and the capacity derivation had therefore only ever been run against
failures, and a happy path nothing has executed is a happy path nobody has
checked. Before test day is a bad time to find out that a passing run renders
wrong.

**Two runs, because one run cannot be both.** That is the finding this file
records, and it is a property of the criteria rather than a defect:

* ``derive_calls_per_node`` refuses a ramp that never saturated. The highest
  concurrency observed is then a floor on the node's capacity and not a measure
  of it, and sizing a fleet from a floor under-provisions.
* ``node_cpu_gate`` fails any step at or above the 70% ceiling.

So the step that makes a capacity figure derivable is the same step that fails
the CPU gate. Measuring capacity means exceeding the ceiling; passing acceptance
means staying under it. An operator needs both runs and the report is right to
describe them differently.

Both fixtures are SYNTHETIC and say so in their own PROVENANCE.md. They are
shaped like the one real capture: a stat file named ``gossipper_<pid>_stats.log``
and no ``summary.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from voicegateway.cli._app import app
from voicegateway.livekit_diag import gates
from voicegateway.repository import node_samples_repository as node_samples
from voicegateway.services.storage_service import StorageService

runner = CliRunner()

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "loadtest"
ACCEPTANCE = FIXTURES / "acceptance-500"
SATURATION = FIXTURES / "saturation-ramp"

NODE = "sfu-1"
SOURCE = "node-exporter"
CORES = 4.0
TOTAL_BYTES = 16 * 2**30


async def _samples(
    db, *, start_ms: int, end_ms: int, cpu: float, memory: float
) -> None:
    """Node samples describing a node at ``cpu`` busy and ``memory`` used.

    CPU is a pair of counters diffed at read time, so the fraction is expressed
    as the RATE the idle counter accrues at: a node 66% busy leaves its idle
    counter climbing at 34% of the core count. Writing a utilisation directly
    would test nothing, because nothing in production stores one.
    """
    idle_rate = CORES * (1.0 - cpu)
    available = int(TOTAL_BYTES * (1.0 - memory))
    rows = []
    for index in range(0, (end_ms - start_ms) // 10_000 + 1):
        elapsed = index * 10
        rows.append(
            node_samples.NodeSampleInput(
                node=NODE,
                source=SOURCE,
                at_ms=start_ms + elapsed * 1000,
                outcome="ok",
                values={
                    "cpu_seconds_total": 1000.0 + CORES * elapsed,
                    "cpu_idle_seconds_total": 800.0 + idle_rate * elapsed,
                    "memory_total_bytes": float(TOTAL_BYTES),
                    "memory_available_bytes": float(available),
                    # The per-process descriptor pair, seeded so file
                    # descriptors are MEASURED here. That isolates the finding:
                    # what is left UNKNOWN is only what nothing can scrape.
                    "process_open_fds": 1200.0,
                    "process_max_fds": 524287.0,
                },
            )
        )
    await node_samples.insert_samples(db, rows)


async def _seed(db_path: Path, windows: list[tuple[int, int, float, float]]) -> None:
    storage = StorageService(db_path=str(db_path))
    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        for start_ms, end_ms, cpu, memory in windows:
            await _samples(db, start_ms=start_ms, end_ms=end_ms, cpu=cpu, memory=memory)
    await storage.aclose()


def _run(
    tmp_path: Path,
    fixture: Path,
    windows,
    *,
    plan: Path | None = None,
    waive: Path | None = None,
):
    """Seed node samples, import, report. Returns the payload and the exits."""
    import asyncio

    db = tmp_path / "throwaway.db"
    config = tmp_path / "voicegw.yaml"
    config.write_text(
        yaml.dump({"cost_tracking": {"enabled": True, "db_path": str(db)}})
    )
    # Seeded BEFORE the import, because correlation happens at import time: a
    # sample written afterwards would never reach the row.
    asyncio.run(_seed(db, windows))

    argv = ["loadtest", "import", str(fixture), "--captured", "--config", str(config)]
    if plan is not None:
        argv += ["--plan", str(plan)]
    imported = runner.invoke(app, argv)

    out = tmp_path / "out"
    report_argv = [
        "loadtest",
        "report",
        fixture.name,
        "--config",
        str(config),
        "--out",
        str(out),
    ]
    if waive is not None:
        report_argv += ["--waive", str(waive)]
    exported = runner.invoke(app, report_argv)
    written = sorted(out.iterdir()) if out.is_dir() else []
    payload = (
        json.loads(next(p for p in written if p.suffix == ".json").read_text())
        if any(p.suffix == ".json" for p in written)
        else None
    )
    return {"imported": imported, "exported": exported, "payload": payload}


def _gate(payload, name: str):
    return [g for g in payload["gates"] if g["gate"] == name]


# --------------------------------------------------------------------------
# The acceptance run: everything passes
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def acceptance(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("acceptance")
    # One window, the whole run, comfortably inside both ceilings.
    return _run(tmp, ACCEPTANCE, [(1_785_661_201_000, 1_785_661_320_000, 0.66, 0.61)])


def test_the_fixture_is_shaped_like_a_real_capture() -> None:
    """Guards the premise. A summary.json here would test a different path."""
    names = {p.name for p in ACCEPTANCE.iterdir()}
    assert "summary.json" not in names
    assert any(n.startswith("gossipper_") and n.endswith("_stats.log") for n in names)


def test_the_acceptance_run_imports(acceptance) -> None:
    assert acceptance["imported"].exit_code == 0, acceptance["imported"].output


def test_every_measurable_gate_passes(acceptance) -> None:
    """Every criterion anything measures is green on this run."""
    measurable = [
        g
        for g in acceptance["payload"]["gates"]
        if g["gate"] in ("call_establishment", "node_cpu", "node_memory")
    ]
    assert measurable
    assert {g["status"] for g in measurable} == {gates.PASS}


def test_the_verdict_is_still_not_pass_and_here_is_why(acceptance) -> None:
    """THE FINDING, and the most important line in this file.

    A verdict of PASS is UNREACHABLE. Two of the three contracted headroom
    resources, RTP ports and network, are scraped by nothing, so they are
    emitted as unmeasured on every run; UNKNOWN outranks PASS, so the run's
    verdict is UNKNOWN however well it went.

    That is honest rather than broken: the criteria require 20% headroom on
    those resources and nothing demonstrated it. But it means a perfect
    500-concurrent run still reports UNKNOWN and exits non-zero, which somebody
    needs to know BEFORE they hand the report over, not after.

    Closing it needs either a scraper for those two, or a written waiver.
    """
    payload = acceptance["payload"]
    assert payload["verdict"]["status"] == gates.UNKNOWN
    unknown = [g for g in payload["gates"] if g["status"] == gates.UNKNOWN]
    assert {g["subject"].split("/")[-1] for g in unknown} == {"rtp_ports", "network"}
    # File descriptors ARE measurable and pass here, which is what makes the
    # other two stand out as the gap rather than as more of the same.
    [fds] = [
        g
        for g in payload["gates"]
        if g["subject"] and g["subject"].endswith("/file_descriptors")
    ]
    assert fds["status"] == gates.PASS


def test_nothing_measures_those_two_resources() -> None:
    """Structural, so it holds beyond this fixture.

    unscraped_headroom_readings emits them unmeasured and no code path anywhere
    supplies a real reading for either.
    """
    readings = gates.unscraped_headroom_readings("sfu-1")
    assert [r.resource for r in readings] == [
        gates.HEADROOM_RTP_PORTS,
        gates.HEADROOM_NETWORK,
    ]
    assert all(r.used is None and r.limit is None for r in readings)
    # And UNKNOWN really does outrank PASS, which is what makes it decisive.
    assert gates.worst_status([gates.PASS, gates.UNKNOWN]) == gates.UNKNOWN


def test_the_establishment_gate_passes(acceptance) -> None:
    """99.9% against a 99.5% floor."""
    [gate] = _gate(acceptance["payload"], "call_establishment")
    assert gate["status"] == gates.PASS
    assert gate["value"] == pytest.approx(0.999)


def test_cpu_and_memory_pass_with_real_percentages(acceptance) -> None:
    """Measured, not UNKNOWN, and carrying the number that decided them."""
    [test] = acceptance["payload"]["tests"]
    assert test["peak_cpu_utilisation"] == pytest.approx(0.66, abs=0.01)
    assert test["peak_memory_utilisation"] == pytest.approx(0.61, abs=0.01)
    for name, expected in (("node_cpu", 0.66), ("node_memory", 0.61)):
        [gate] = _gate(acceptance["payload"], name)
        assert gate["status"] == gates.PASS, (name, gate["detail"])
        assert gate["value"] == pytest.approx(expected, abs=0.01)


def test_the_cli_still_exits_non_zero(acceptance) -> None:
    """Follows from the verdict, and is the behaviour a pipeline will see.

    Not a defect: UNKNOWN is not a demonstration. It is recorded here so the
    exit code on test day is not a surprise.
    """
    assert acceptance["exported"].exit_code != 0


def test_a_passing_run_has_no_capacity_figure(acceptance) -> None:
    """The finding, asserted rather than described.

    This run never exceeded the ceiling, so its capacity is a floor and the
    derivation refuses. A number here would be the wrong one to size on.
    """
    capacity = acceptance["payload"]["capacity"]
    assert capacity["calls_per_node"] is None
    assert "never saturated" in capacity["reason"]


# --------------------------------------------------------------------------
# The sizing run: a capacity table, at the cost of the CPU gate
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def saturation(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("saturation")
    # One window per step, 90s apart, CPU climbing past the ceiling at the last.
    base = 1_785_661_201_000
    windows = [
        (
            base + index * 90_000,
            base + index * 90_000 + 59_000,
            cpu,
            0.40 + index * 0.05,
        )
        for index, cpu in enumerate((0.31, 0.48, 0.66, 0.79))
    ]
    return _run(tmp, SATURATION, windows, plan=SATURATION / "plan.json")


def test_every_step_imported(saturation) -> None:
    assert saturation["imported"].exit_code == 0, saturation["imported"].output
    assert len(saturation["payload"]["tests"]) == 4


def test_the_ramp_does_not_plateau(saturation) -> None:
    """A plateaued ramp would prove the refusal rather than the derivation."""
    peaks = [t["peak_concurrency"] for t in saturation["payload"]["tests"]]
    assert peaks == [100, 150, 200, 250]
    assert peaks == sorted(peaks)


def test_the_declared_targets_reached_the_rows(saturation) -> None:
    """Supplied by the operator, because no artifact records them."""
    targets = [t["target_concurrency"] for t in saturation["payload"]["tests"]]
    assert targets == [100, 150, 200, 250]


def test_the_capacity_figure_is_derived(saturation) -> None:
    """200: the highest concurrency sustained at or under 70% CPU."""
    capacity = saturation["payload"]["capacity"]
    assert capacity["calls_per_node"] == 200


def test_the_table_carries_every_contracted_tier(saturation) -> None:
    tiers = {
        t["target_concurrency"]: t for t in saturation["payload"]["capacity"]["tiers"]
    }
    assert sorted(tiers) == [100, 150, 300, 500]


def test_every_tier_carries_a_spare_node(saturation) -> None:
    """A tier sized without one has no node it can afford to lose."""
    for tier in saturation["payload"]["capacity"]["tiers"]:
        assert tier["spare_nodes"] == 1
        assert tier["nodes"] == tier["nodes_for_load"] + 1


def test_the_headline_tier_matches_the_formula(saturation) -> None:
    """500 at C=200: ceil(500 / (0.85 x 200)) + 1 = ceil(2.94) + 1 = 4."""
    tiers = {
        t["target_concurrency"]: t for t in saturation["payload"]["capacity"]["tiers"]
    }
    assert tiers[500]["usable_per_node"] == pytest.approx(170.0)
    assert tiers[500]["nodes"] == 4


def test_the_sizing_run_fails_its_cpu_gate(saturation) -> None:
    """The cost of the figure, and it must be visible rather than smoothed away.

    The 79% step is what made the derivation possible and it breaches the
    contracted ceiling. A report that showed the table without this failure
    would be describing a fleet nobody demonstrated.
    """
    statuses = [g["status"] for g in _gate(saturation["payload"], "node_cpu")]
    assert gates.FAIL in statuses
    assert saturation["payload"]["verdict"]["status"] == gates.FAIL
    assert saturation["exported"].exit_code != 0


def test_the_steps_under_the_ceiling_still_pass(saturation) -> None:
    """Non-vacuous: only the saturating step fails, not the whole ramp."""
    statuses = [g["status"] for g in _gate(saturation["payload"], "node_cpu")]
    assert statuses.count(gates.PASS) == 3
    assert statuses.count(gates.FAIL) == 1


# --------------------------------------------------------------------------
# The two runs are the finding
# --------------------------------------------------------------------------


def test_a_capacity_figure_requires_breaching_the_ceiling(
    acceptance, saturation
) -> None:
    """Stated once, directly, from the two runs side by side.

    The run that passes has no capacity figure. The run with a capacity figure
    does not pass. Anyone reading one report and expecting both is going to be
    disappointed on test day, so it is written down here.
    """
    assert acceptance["payload"]["capacity"]["calls_per_node"] is None
    assert "never saturated" in acceptance["payload"]["capacity"]["reason"]

    assert saturation["payload"]["capacity"]["calls_per_node"] == 200
    assert saturation["payload"]["verdict"]["status"] == gates.FAIL


# --------------------------------------------------------------------------
# The best achievable outcome: WAIVED, in writing
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def waived(tmp_path_factory):
    """The acceptance run with the two unscrapeable resources waived."""
    tmp = tmp_path_factory.mktemp("waived")
    waiver = tmp / "waivers.json"
    waiver.write_text(
        json.dumps(
            {
                "resource_headroom/sfu-1/node-exporter/rtp_ports": (
                    "no RTP-port exporter was funded for this run, agreed in "
                    "writing before test day"
                ),
                "resource_headroom/sfu-1/node-exporter/network": (
                    "no network-headroom exporter was funded for this run, "
                    "agreed in writing before test day"
                ),
            }
        )
    )
    return _run(
        tmp,
        ACCEPTANCE,
        [(1_785_661_201_000, 1_785_661_320_000, 0.66, 0.61)],
        waive=waiver,
    )


def test_a_waiver_records_the_reason_rather_than_hiding_the_gate(waived) -> None:
    """The checklist's requirement: waived in writing, never a silent pass."""
    payload = waived["payload"]
    waived_gates = [g for g in payload["gates"] if g["status"] == gates.WAIVED]
    assert len(waived_gates) == 2
    for gate in waived_gates:
        assert "agreed in writing" in gate["detail"]
        assert gate["waiver_reason"]


def test_a_waiver_does_not_become_a_pass(waived) -> None:
    """WAIVED outranks PASS, so the verdict says a requirement was set aside."""
    assert waived["payload"]["verdict"]["status"] == gates.WAIVED
    assert waived["payload"]["verdict"]["status"] != gates.PASS


def test_a_waived_run_still_exits_non_zero(waived) -> None:
    """A pipeline going green on a waiver has dropped the requirement."""
    assert waived["exported"].exit_code != 0


def test_the_measured_gates_are_untouched_by_the_waiver(waived) -> None:
    """Non-vacuous: waiving two headroom gates must not waive anything else."""
    measurable = [
        g
        for g in waived["payload"]["gates"]
        if g["gate"] in ("call_establishment", "node_cpu", "node_memory")
    ]
    assert {g["status"] for g in measurable} == {gates.PASS}


def test_a_waiver_naming_no_gate_is_refused(tmp_path) -> None:
    """A typo would leave the gate unwaived and the waiver unrecorded."""
    waiver = tmp_path / "waivers.json"
    waiver.write_text(
        json.dumps({"resource_headroom/sfu-1/node-exporter/rtp_port": "typo"})
    )
    result = _run(
        tmp_path,
        ACCEPTANCE,
        [(1_785_661_201_000, 1_785_661_320_000, 0.66, 0.61)],
        waive=waiver,
    )
    assert result["exported"].exit_code == 2
    assert result["payload"] is None, "a refused waiver must not write a report"
