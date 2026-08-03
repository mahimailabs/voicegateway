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
from voicegateway.loadtest import judge
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

# The media-port range this node was configured with, and how much of it the run
# occupied. Seeded because RTP-port headroom is a MEASUREMENT now: it comes from
# the media_ports_in_use / media_ports_total pair rather than being emitted as a
# scope exclusion, which is what it was when this file was written.
MEDIA_PORTS_TOTAL = 10001.0
MEDIA_PORTS_IN_USE = 1200.0

# Throughput, as the cumulative byte counters a node_exporter publishes. Read as
# a RATE over the window, so the value has to be monotonic in absolute time
# rather than restarting per window: a counter that goes backwards is a reset,
# and read_counter_rate reports a reset as "cannot say" rather than as a number.
COUNTER_ORIGIN_S = 1_785_661_200
RX_BYTES_PER_SECOND = 50_000_000.0
TX_BYTES_PER_SECOND = 60_000_000.0

# The instance type's PUBLISHED baseline, in bits per second, both directions.
# Declared rather than scraped, and that is the whole design: the ENA driver
# reports no link speed, so there is no measurable denominator and an operator
# writes the published figure down at import. 400 Mbps in and 480 Mbps out
# against 3.125 Gbps leaves 87% and 85% headroom, comfortably over the 20% floor.
DECLARED_BASELINE_BPS = 3_125_000_000.0


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
        at_ms = start_ms + elapsed * 1000
        since_origin = at_ms // 1000 - COUNTER_ORIGIN_S
        rows.append(
            node_samples.NodeSampleInput(
                node=NODE,
                source=SOURCE,
                at_ms=at_ms,
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
                    # Media ports and network, seeded for the SAME reason and
                    # this is what changed: both used to be scraped by nothing
                    # and reported as scope exclusions, so a perfect run could
                    # never reach PASS. Both are real columns now, so the run
                    # that demonstrates the acceptance criteria has to carry
                    # them or it is demonstrating less than it claims.
                    "media_ports_in_use": MEDIA_PORTS_IN_USE,
                    "media_ports_total": MEDIA_PORTS_TOTAL,
                    "network_receive_bytes_total": RX_BYTES_PER_SECOND
                    * since_origin,
                    "network_transmit_bytes_total": TX_BYTES_PER_SECOND
                    * since_origin,
                    # The hypervisor's five shaping counters, flat at zero: this
                    # node was never throttled during the window. Flat rather
                    # than absent, because an unread counter is UNKNOWN and a
                    # zero DELTA is a measured clean window.
                    "ethtool_bw_in_allowance_exceeded": 0.0,
                    "ethtool_bw_out_allowance_exceeded": 0.0,
                    "ethtool_pps_allowance_exceeded": 0.0,
                    "ethtool_conntrack_allowance_exceeded": 0.0,
                    "ethtool_linklocal_allowance_exceeded": 0.0,
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
    declare_network_baseline: bool = False,
):
    """Seed node samples, import, report. Returns the payload and the exits.

    ``declare_network_baseline`` writes the per-node published link baseline and
    passes it to the import, which is the only way a bandwidth headroom figure
    can exist: nothing on the host reports link speed, so an undeclared node
    reaches the gate as UNKNOWN rather than being divided by a guess.
    """
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
    if declare_network_baseline:
        baseline = tmp_path / "network-baseline.json"
        baseline.write_text(
            json.dumps(
                {
                    NODE: {
                        "in_bps": DECLARED_BASELINE_BPS,
                        "out_bps": DECLARED_BASELINE_BPS,
                    }
                }
            )
        )
        argv += ["--network-baseline", str(baseline)]
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
    # One window, the whole run, comfortably inside both ceilings. The network
    # baseline is declared, so the bandwidth denominator exists and the two
    # network gates are judged rather than reported as having nothing to divide
    # by. That declaration is the operator's job and this is the run that shows
    # what it buys.
    return _run(
        tmp,
        ACCEPTANCE,
        [(1_785_661_201_000, 1_785_661_320_000, 0.66, 0.61)],
        declare_network_baseline=True,
    )


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

    WHAT CHANGED. The finding used to be that PASS was unreachable because two
    of the three contracted headroom resources, RTP ports and network, were
    scraped by NOTHING and therefore reported unmeasured on every run. Both are
    measured now, from real columns, and on this run both are green. So the old
    assertion that ``rtp_ports`` and ``network`` are in the UNKNOWN set is false
    for the best possible reason, and asserting it would now be asserting that
    the coverage is missing.

    What survives is the same shape of finding at a smaller size. A verdict of
    PASS is STILL unreachable on this fixture, and the reasons are now of three
    kinds, none of them a pass:

    * ``pps``, which is permanent. No per-instance-type packets-per-second
      allowance is published by anyone, so there is no denominator. Wiring an
      exporter does not lift it.
    * two dependencies nobody configured on this fixture.
    * four lifecycle criteria whose columns this fixture's samples do not carry.

    That still means a perfect 500-concurrent run reports UNKNOWN and exits
    non-zero, which somebody needs to know BEFORE they hand the report over.
    Closing it needs a written waiver, and pps can only ever be closed that way.
    """
    payload = acceptance["payload"]
    assert payload["verdict"]["status"] == gates.UNKNOWN
    unknown = [g for g in payload["gates"] if g["status"] == gates.UNKNOWN]
    assert {g["subject"].split("/")[-1] for g in unknown} == {
        # PERMANENTLY unmeasurable: the denominator is published by nobody.
        gates.HEADROOM_PPS,
        # Unconfigured on this fixture: no exporter, no health endpoint.
        "redis",
        "health_endpoint",
        # Restarts and OOM: the fixture's samples carry no process start time
        # and no OOM counter, so neither could be ruled out. Staleness IS
        # evaluated here and passes.
        "node-exporter",
        # Return to baseline: this fixture records nothing outside the test
        # window, so no baseline was established and nothing shows the
        # resources came back.
        "memory_used_bytes",
        "filefd_allocated",
        "sockstat_udp_inuse",
    }
    # THE COMPANION, and the reason the set above got shorter. The three
    # resources that left it are PASSING measurements on this run, not silent
    # omissions, and each carries the number that decided it.
    measured = {
        g["subject"]: g
        for g in payload["gates"]
        if g["gate"] == gates.HEADROOM_GATE and g["status"] == gates.PASS
    }
    assert set(measured) == {
        f"{NODE}/{SOURCE}/file_descriptors",
        f"{NODE}/{SOURCE}/{gates.HEADROOM_RTP_PORTS}",
        f"{NODE}/{gates.HEADROOM_NETWORK_IN}",
        f"{NODE}/{gates.HEADROOM_NETWORK_OUT}",
    }
    for subject, gate in measured.items():
        assert gate["value"] >= gates.MIN_HEADROOM_FRACTION, subject
    # And the hypervisor never throttled this node, measured as a zero DELTA
    # rather than assumed from an unread counter.
    [allowance] = [
        g for g in payload["gates"] if g["gate"] == gates.NETWORK_ALLOWANCE_GATE
    ]
    assert allowance["status"] == gates.PASS
    assert allowance["value"] == 0.0


def test_both_network_directions_are_measured_separately(acceptance) -> None:
    """Ingress and egress are separate credit buckets, so separate rows.

    This replaces a test that asserted nothing could measure either resource.
    The stronger claim now available is that they are measured, judged
    INDEPENDENTLY, and cannot collide: one combined ``network`` row would have
    had to pick a direction, and whichever it picked is the one nobody asked
    about. The two values here differ, which is what makes the split non-vacuous.
    """
    payload = acceptance["payload"]
    inbound = [
        g
        for g in payload["gates"]
        if g["subject"] == f"{NODE}/{gates.HEADROOM_NETWORK_IN}"
    ]
    outbound = [
        g
        for g in payload["gates"]
        if g["subject"] == f"{NODE}/{gates.HEADROOM_NETWORK_OUT}"
    ]
    assert len(inbound) == 1
    assert len(outbound) == 1
    # 400 Mbps in and 480 Mbps out against the same declared 3.125 Gbps.
    assert inbound[0]["value"] == pytest.approx(0.872)
    assert outbound[0]["value"] == pytest.approx(0.8464)
    assert inbound[0]["value"] != outbound[0]["value"]
    assert gates.HEADROOM_NETWORK_IN != gates.HEADROOM_NETWORK_OUT
    # And UNKNOWN really does outrank PASS, which is what makes any remaining
    # unmeasured criterion decisive for the verdict.
    assert gates.worst_status([gates.PASS, gates.UNKNOWN]) == gates.UNKNOWN


def test_pps_is_the_one_resource_nothing_will_ever_measure() -> None:
    """Structural, so it holds beyond this fixture.

    The old test here asserted that nothing measured RTP ports or network. That
    is exactly the fact that changed, so it is replaced by the one exclusion
    that no exporter can lift, with its reason asserted to say the denominator
    is UNPUBLISHED rather than merely uncollected. A reason phrased as "nothing
    scrapes it" would read as work somebody could do.
    """
    excluded = judge.excluded_headroom_resources()
    assert sorted(excluded) == [gates.HEADROOM_PPS]
    reason = excluded[gates.HEADROOM_PPS]
    assert "no denominator" in reason
    assert "not a gap awaiting work" in reason
    assert "no per-instance-type PPS allowance is published" in reason
    # THE COMPANION. The three that used to be here are measurable, and the
    # unmeasured-reading helper still files them for a caller that scraped
    # none of them, so an absent scrape is still visible rather than silent.
    for resource in (
        gates.HEADROOM_RTP_PORTS,
        gates.HEADROOM_NETWORK_IN,
        gates.HEADROOM_NETWORK_OUT,
    ):
        assert resource not in excluded, resource
    readings = gates.unscraped_headroom_readings(NODE)
    assert [r.resource for r in readings] == [
        gates.HEADROOM_RTP_PORTS,
        gates.HEADROOM_NETWORK_IN,
        gates.HEADROOM_NETWORK_OUT,
    ]
    assert all(r.used is None and r.limit is None for r in readings)


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
    """The acceptance run with every unmeasured criterion declared.

    SEVEN now, not eight, and the one that left is the point. The old map
    waived ``resource_headroom/fleet/rtp_ports`` and
    ``resource_headroom/fleet/network``, two fleet-level rows that existed
    because nothing could measure either resource. RTP ports and network are
    measured on this run and PASS, so those keys match no gate, and a waiver key
    matching no gate is REFUSED by design (see
    :func:`test_a_waiver_naming_no_gate_is_refused`): leaving them in would exit
    2 and write no report at all.

    What replaces them is nothing. A resource that is measured needs no waiver,
    which is the improvement stated as a diff in this file.

    ``resource_headroom/fleet/pps`` is the one headroom key still here, and it is
    the only one that can NEVER be retired by wiring an exporter: no
    per-instance-type PPS allowance is published anywhere, so a written waiver
    is the only way that criterion is ever closed.
    """
    tmp = tmp_path_factory.mktemp("waived")
    waiver = tmp / "waivers.json"
    waiver.write_text(
        json.dumps(
            {
                "resource_headroom/fleet/pps": (
                    "packets-per-second headroom has no published denominator on "
                    "any instance type, agreed in writing before test day as "
                    "permanently outside scope"
                ),
                # Declared, not silent. The Redis and health-check criteria were
                # added after this fixture was written, and an unconfigured
                # dependency is deliberately UNKNOWN rather than a pass: a run
                # that never looked at Redis must not be indistinguishable from
                # one that checked and found it healthy. A single-node
                # deployment genuinely has no Redis, and the honest way to say
                # so is a recorded waiver a reviewer can read, not silence.
                "sustained_health/fleet/redis": (
                    "single-node deployment with no Redis, declared before test "
                    "day rather than left unevaluated"
                ),
                "sustained_health/fleet/health_endpoint": (
                    "no health endpoint configured on this deployment, declared "
                    "before test day rather than left unevaluated"
                ),
                "process_lifecycle/sfu-1/node-exporter": (
                    "no process start time or OOM counter in this scrape set, "
                    "declared before test day"
                ),
                # Return to baseline, declared for the same reason: this
                # fixture records nothing outside the test window, so no
                # baseline was ever established to return to. Restarts and
                # staleness ARE evaluated on this fixture and are not waived.
                "return_to_baseline/fleet/memory_used_bytes": (
                    "no idle samples outside the window, declared before test day"
                ),
                "return_to_baseline/fleet/filefd_allocated": (
                    "no idle samples outside the window, declared before test day"
                ),
                "return_to_baseline/fleet/sockstat_udp_inuse": (
                    "no idle samples outside the window, declared before test day"
                ),
            }
        )
    )
    return _run(
        tmp,
        ACCEPTANCE,
        [(1_785_661_201_000, 1_785_661_320_000, 0.66, 0.61)],
        waive=waiver,
        declare_network_baseline=True,
    )


def test_a_waiver_records_the_reason_rather_than_hiding_the_gate(waived) -> None:
    """The checklist's requirement: waived in writing, never a silent pass."""
    payload = waived["payload"]
    waived_gates = [g for g in payload["gates"] if g["status"] == gates.WAIVED]
    # Seven, down from eight, and every one of them names a real gate: the CLI
    # exits 2 on a waiver key that matches nothing, so this count is also the
    # assertion that no key here is stale.
    assert len(waived_gates) == 7
    assert waived["exported"].exit_code != 2
    for gate in waived_gates:
        # The property, rather than one phrase: a waiver carries a human reason
        # and that reason is visible in the rendered detail. A waiver a reviewer
        # cannot read is the silent pass this exists to prevent.
        assert gate["waiver_reason"]
        assert gate["waiver_reason"] in gate["detail"]


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
    waiver.write_text(json.dumps({"resource_headroom/fleet/rtp_port": "typo"}))
    result = _run(
        tmp_path,
        ACCEPTANCE,
        [(1_785_661_201_000, 1_785_661_320_000, 0.66, 0.61)],
        waive=waiver,
    )
    assert result["exported"].exit_code == 2
    assert result["payload"] is None, "a refused waiver must not write a report"
