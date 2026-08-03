"""``voicegw loadtest import``: ingest an external generator's run artifacts.

VoiceGateway does not place calls. This reads what a load generator wrote and
records it as one run with a row per test, which is the shape a load-test report
is owed.
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path

import typer
from rich.table import Table

from voicegateway.cli._app import app, console
from voicegateway.cli.base_cli import BaseCli
from voicegateway.livekit_diag.gates import (
    MAX_NODE_CPU_UTILISATION,
    exit_code,
)
from voicegateway.livekit_diag.gates import waive as gates_waive
from voicegateway.livekit_diag.run_report import appendix_entry
from voicegateway.loadtest.aggregation import TestAggregate, peak_label
from voicegateway.loadtest.artifacts import ArtifactError
from voicegateway.loadtest.capacity import (
    RampStep,
    capacity_table,
    derive_calls_per_node,
)
from voicegateway.loadtest.importer import (
    build_plan,
    limitations_from_notes,
    network_baselines_from_notes,
    observations_for,
    plan_from_notes,
)
from voicegateway.loadtest.judge import (
    excluded_headroom_resources,
    judge_run,
)

_cli = BaseCli()

loadtest_app = typer.Typer(
    help="Import and inspect load-generator run artifacts.",
    no_args_is_help=True,
)
app.add_typer(loadtest_app, name="loadtest")


def _cell(value: object) -> str:
    """Render a measurement, showing absence AS absence.

    A dash, never a 0. A 0 in peak concurrency describes a test that carried no
    calls, which is a different claim from "the artifacts did not say", and a
    table that renders both the same way makes the second one unfalsifiable.
    """
    return "-" if value is None else str(value)


def _ratio(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.3f}%"


def _pct(value: float | None) -> str:
    """A utilisation fraction as a percentage, or "not measured".

    Never "0%" for an absent reading: a node nobody scraped is not an idle one.
    """
    return "not measured" if value is None else f"{value * 100:.1f}%"


def _cpu_samples(aggregate: TestAggregate) -> int:
    """How many points the CPU peak came from, for labelling it honestly."""
    return max((r.samples for r in aggregate.cpu_readings), default=0)


@loadtest_app.command("import")
def import_run(
    directory: Path = typer.Argument(..., help="Directory of run artifacts"),
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
    run_id: str = typer.Option(
        None, "--run-id", help="Run id (defaults to the directory name)"
    ),
    project: str = typer.Option("default", "--project", "-p", help="Project id"),
    label: str = typer.Option(None, "--label", "-l", help="Human label for the run"),
    captured: bool = typer.Option(
        False,
        "--captured",
        help=(
            "Declare these artifacts came from a real run. Without it the run "
            "is recorded as synthetic and every report stamps itself so."
        ),
    ),
    node: str = typer.Option(
        None, "--node", help="Correlate against one node only (default: all)"
    ),
    targets: Path = typer.Option(
        None,
        "--plan",
        help=(
            "JSON mapping test name to the concurrency it ASKED for, e.g. "
            "'{\"ramp-250\": 250}'. Not recorded by any artifact: it lives in "
            "the generator's scenario file. Without it the capacity table "
            "cannot be derived across a multi-step ramp."
        ),
    ),
    network_baseline: Path = typer.Option(
        None,
        "--network-baseline",
        help=(
            "JSON mapping NODE name to its published network baseline in bits "
            'per second, e.g. \'{"sip-1": {"in_bps": 3125000000, "out_bps": '
            "3125000000}}'. Nothing measures it: the ENA driver reports no link "
            "speed. A node with no entry has no denominator and reports UNKNOWN."
        ),
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be written, write nothing"
    ),
) -> None:
    """Import one directory of artifacts as a run with a row per test."""
    declared = _plan_from_file(targets) if targets is not None else {}
    baselines = (
        _network_baselines_from_file(network_baseline)
        if network_baseline is not None
        else {}
    )
    try:
        plan = build_plan(
            directory,
            run_id=run_id,
            project=project,
            label=label,
            captured=captured,
            now_ms=int(time.time() * 1000),
            declared_targets=declared,
            declared_network_baselines=baselines,
        )
    except ArtifactError as exc:
        # Named errors carry which surface disagreed and how, so they are shown
        # rather than flattened into a generic import failure.
        _cli.fail(f"{type(exc).__name__}: {exc}", code=2)

    unknown = sorted(set(declared) - {t.name for t in plan.tests})
    if unknown:
        _cli.fail(
            f"--plan names {unknown} which no test in this run is called. "
            f"Tests here: {sorted(t.name for t in plan.tests)}. A typo would "
            "otherwise leave the ramp undeclared and the capacity table missing.",
            code=2,
        )

    table = Table(title=f"Run {plan.run.id} ({len(plan.tests)} tests)")
    table.add_column("#", justify="right")
    table.add_column("Test", style="cyan")
    table.add_column("Peak conc.", justify="right")
    table.add_column("Attempted", justify="right")
    table.add_column("Established", justify="right")
    table.add_column("Failed", justify="right")
    for test, parsed in zip(plan.tests, plan.parsed, strict=True):
        table.add_row(
            str(test.sequence),
            test.name,
            _cell(test.peak_concurrency),
            _cell(test.attempted_calls),
            _ratio(parsed.establishment_ratio),
            _cell(test.failed_calls),
        )
    console.print(table)

    for parsed in plan.parsed:
        if parsed.reported_ratio_disagrees:
            _cli.warn(
                f"{parsed.name}: the generator's reported success_ratio "
                f"({parsed.reported_success_ratio}) contradicts its own counts "
                f"({parsed.succeeded_calls}/{parsed.attempted_calls}). One of "
                "them was misread; trust neither until a human looks."
            )

    # Branch on the checksum itself rather than on plan.is_synthetic: presence
    # of the checksum IS the derivation, and reading it here is what lets the
    # type narrow instead of being asserted.
    checksum = plan.run.artifact_sha256
    if checksum is None:
        _cli.warn(
            "SYNTHETIC: recorded without --captured, so every report built from "
            "this run stamps itself as not a deliverable."
        )
    else:
        _cli.info(f"Captured artifacts, sha256 {checksum[:16]}...")

    if plan.limitations:
        console.print("\n[dim]Not measured by these artifacts:[/dim]")
        for line in plan.limitations:
            console.print(f"  [dim]- {line}[/dim]")

    observations = sum(len(observations_for(p)) for p in plan.parsed)
    console.print(f"\n[dim]{observations} per-call observations to file.[/dim]")

    if dry_run:
        _cli.info("Dry run: nothing written.")
        raise typer.Exit(0)

    gw = _cli.require_gateway(config)
    storage = _cli.require_storage(gw)

    async def _write() -> list[str]:
        await storage.upsert_load_run(plan.run)
        correlated: list[str] = []
        for test in plan.tests:
            # Correlate BEFORE writing, so the row lands complete rather than
            # being written once without the fleet columns and again with them.
            aggregate = await storage.correlate_load_run_test(
                started_at_ms=test.started_at_ms,
                ended_at_ms=test.ended_at_ms,
                node=node,
            )
            if aggregate is None:
                # No window, so nothing to overlap. The columns stay NULL.
                await storage.upsert_load_run_test(test)
                continue
            await storage.upsert_load_run_test(
                replace(
                    test,
                    peak_cpu_utilisation=aggregate.peak_cpu_utilisation,
                    peak_memory_utilisation=aggregate.peak_memory_utilisation,
                    node_samples_in_window=aggregate.node_samples_in_window,
                )
            )
            if aggregate.node_samples_in_window:
                correlated.append(
                    f"{test.name}: {aggregate.node_samples_in_window} node samples "
                    f"overlap this test's window across {aggregate.nodes_seen} "
                    f"targets, cpu {_pct(aggregate.peak_cpu_utilisation)} "
                    f"({peak_label(_cpu_samples(aggregate))}), "
                    f"memory {_pct(aggregate.peak_memory_utilisation)}"
                )
        return correlated

    correlated = _cli.async_run(_write())
    if correlated:
        console.print("\n[dim]Fleet during each test (overlap, not attribution):[/dim]")
        for line in correlated:
            console.print(f"  [dim]- {line}[/dim]")
    else:
        _cli.warn(
            "No node samples overlap these test windows, so peak CPU and memory "
            "are not measured. They stay NULL rather than reading 0."
        )
    _cli.success(f"Imported {plan.run.id}: {len(plan.tests)} tests.")


@loadtest_app.command("runs")
def list_runs(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
    project: str = typer.Option(None, "--project", "-p", help="Filter by project"),
    limit: int = typer.Option(20, "--limit", "-n", help="How many runs to show"),
) -> None:
    """List imported load runs, newest first."""
    gw = _cli.require_gateway(config)
    storage = _cli.require_storage(gw)
    rows = _cli.async_run(storage.list_load_runs(project=project, limit=limit))
    if not rows:
        console.print("[dim]No load runs imported yet.[/dim]")
        raise typer.Exit(0)

    table = Table(title=f"Load runs ({len(rows)})")
    table.add_column("Run", style="cyan")
    table.add_column("Project")
    table.add_column("Tool")
    table.add_column("Provenance")
    for row in rows:
        # Derived from the checksum, never from a stored flag, so nothing can
        # claim measured-ness without holding the artifact that proves it.
        measured = bool(row.get("artifact_sha256"))
        table.add_row(
            str(row.get("id", "-")),
            str(row.get("project", "-")),
            str(row.get("tool") or "-"),
            "[green]measured[/green]" if measured else "[yellow]synthetic[/yellow]",
        )
    console.print(table)


def _plan_from_file(path: Path) -> dict[str, dict[str, float]]:
    """Read what each step ASKED for, which no artifact records.

    ``target_concurrency`` and the arrival rate live in the generator's scenario
    file. A scenario file is not an artifact, so nothing on the import path can
    recover either, and without a target :func:`derive_calls_per_node` refuses
    across a multi-step ramp: "no plateau detected" from a ramp that declared
    nothing means the check never ran, not that the ramp kept climbing.

    This is the operator DECLARING it. A declaration is not a measurement and is
    kept apart from one at every step: the peak concurrency a step reached still
    comes from the stat file and is never overwritten here, and a declared
    target that the run did not reach produces a plateau finding rather than a
    capacity figure. What the operator supplies is only what was requested,
    which is the half a machine cannot see.

    Two accepted shapes, so the short one stays available::

        {"ramp-100": 100}
        {"ramp-100": {"target_concurrency": 100, "rate_per_second": 3.23,
                      "hold_seconds": 60}}

    The rate matters because it is what lets the plan itself be checked: a step
    asking for more concurrency than rate times hold can sustain is unreachable
    before the run starts, and saying so beforehand is worth more than
    diagnosing it after.

    A name that matches no test is refused rather than ignored, because a typo
    would otherwise leave the ramp undeclared and the table missing.
    """
    try:
        raw = json.loads(path.read_text())
    except OSError as exc:
        raise typer.BadParameter(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise typer.BadParameter(
            f"{path}: expected an object mapping test name to its declared plan"
        )

    plan: dict[str, dict[str, float]] = {}
    for name, value in raw.items():
        step = value if isinstance(value, dict) else {"target_concurrency": value}
        target = step.get("target_concurrency")
        if not isinstance(target, int) or isinstance(target, bool) or target <= 0:
            raise typer.BadParameter(
                f"{path}: {name!r} needs a positive integer target_concurrency, "
                f"got {target!r}"
            )
        entry: dict[str, float] = {"target_concurrency": target}
        for optional in ("rate_per_second", "hold_seconds"):
            if step.get(optional) is None:
                continue
            number = step[optional]
            if not isinstance(number, (int, float)) or isinstance(number, bool):
                raise typer.BadParameter(
                    f"{path}: {name!r} {optional} must be a number, got {number!r}"
                )
            if number <= 0:
                raise typer.BadParameter(
                    f"{path}: {name!r} {optional} must be positive, got {number!r}"
                )
            entry[optional] = float(number)
        unknown = sorted(
            set(step) - {"target_concurrency", "rate_per_second", "hold_seconds"}
        )
        if unknown:
            raise typer.BadParameter(
                f"{path}: {name!r} has unknown field(s) {unknown}. A misspelled "
                "field would be silently ignored and the declaration incomplete."
            )
        plan[str(name)] = entry
    return plan


#: The only two fields a declared baseline may carry. Both are required and
#: neither has a default, because the point of the file is to be the one place a
#: figure nobody can measure is written down by hand.
_NETWORK_BASELINE_DIRECTIONS = ("in_bps", "out_bps")


def _network_baselines_from_file(path: Path) -> dict[str, dict[str, float]]:
    """Read the per-node link capacity that nothing on the host reports.

    There is no measurable denominator for bandwidth headroom on these nodes. The
    AWS ENA driver publishes no link speed, so ``node_network_speed_bytes`` is
    absent for the interface carrying the media, and every attempt to compute a
    utilisation from the byte counters alone divides by something that does not
    exist. The only figure that does exist is the baseline AWS publishes for the
    instance type, in a document, which is why an operator declares it here
    rather than anything scraping it.

    Shape, keyed by NODE name::

        {"sip-1": {"in_bps": 3125000000, "out_bps": 3125000000}}

    In bits per second, both directions, always both. The two are separate
    allowances on the instance and separate counters on the host, and letting one
    number cover both would put an assumption inside a declaration that exists to
    hold none.

    THIS FILE IS THE ONLY SOURCE. No instance type is mapped to a bandwidth
    anywhere in this code, and none may be added. A lookup table would hand a
    denominator to a node whose instance type nobody verified, and the report
    would then carry a percentage of a guess where it owes an UNKNOWN. An
    undeclared node stays undeclared all the way through to the gate.

    A declaration is not a measurement, and this one under-claims by
    construction: the published baseline is a FLOOR, since the instance can burst
    well above it (a c7i.2xlarge baselines at 3.125 Gbps and peaks at 12.5), so a
    utilisation computed against it reads higher than the truth. That is the safe
    direction for a headroom check, and it is recorded in the run notes so nobody
    downstream reads the figure as a measured link capacity.

    A misspelled field is refused rather than ignored, because a silently dropped
    direction is a node that looks declared and is not.
    """
    try:
        raw = json.loads(path.read_text())
    except OSError as exc:
        raise typer.BadParameter(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise typer.BadParameter(
            f"{path}: expected an object mapping node name to its declared "
            "network baseline, e.g. "
            '{"sip-1": {"in_bps": 3125000000, "out_bps": 3125000000}}'
        )

    baselines: dict[str, dict[str, float]] = {}
    for node, value in raw.items():
        if not isinstance(value, dict):
            raise typer.BadParameter(
                f"{path}: {node!r} must be an object carrying in_bps and "
                f"out_bps, got {value!r}. There is no short form: ingress and "
                "egress are separate allowances, and one figure covering both "
                "would be an assumption nobody declared."
            )
        # Unknown keys are caught BEFORE the missing-field check, so that
        # {"in_bps": x, "out_bsp": y} names the typo rather than reporting the
        # field it caused to go missing.
        unknown = sorted(set(value) - set(_NETWORK_BASELINE_DIRECTIONS))
        if unknown:
            raise typer.BadParameter(
                f"{path}: {node!r} has unknown field(s) {unknown}. Known fields "
                f"are {list(_NETWORK_BASELINE_DIRECTIONS)}; a misspelled one "
                "would be silently ignored and the declaration incomplete."
            )
        entry: dict[str, float] = {}
        for direction in _NETWORK_BASELINE_DIRECTIONS:
            number = value.get(direction)
            if not isinstance(number, (int, float)) or isinstance(number, bool):
                raise typer.BadParameter(
                    f"{path}: {node!r} needs a numeric {direction} in bits per "
                    f"second, got {number!r}. It cannot be defaulted: no "
                    "instance-type lookup exists here, and a node with no "
                    "declared baseline reports UNKNOWN rather than a percentage "
                    "of a guess."
                )
            if number <= 0:
                raise typer.BadParameter(
                    f"{path}: {node!r} {direction} must be positive, got "
                    f"{number!r}. A zero baseline is not a saturated link, it is "
                    "a denominator nobody supplied."
                )
            entry[direction] = float(number)
        baselines[str(node)] = entry
    return baselines


def _waivers_from_file(path: Path) -> dict[str, str]:
    """Read gates the operator decided IN WRITING not to hold this run to.

    The checklist requires that a threshold nobody funded is recorded as waived
    rather than passed silently. :func:`gates.waive` implements that and nothing
    reached it from here, so the only way to report an unscraped resource was
    UNKNOWN, with no way to say who accepted it or why.

    That matters more than it sounds. Two of the three contracted headroom
    resources, RTP ports and network, are scraped by nothing, so every run
    reports UNKNOWN for them and a run's verdict can never be PASS. A waiver
    does not turn that into a pass either: WAIVED outranks PASS and exits
    non-zero. What it changes is that the report says WHY the requirement was
    set aside, and by whom, instead of looking like a measurement nobody took.

    Keys are ``gate`` or ``gate/subject``, the more specific winning::

        {"resource_headroom/fleet/rtp_ports": "not funded for this run, agreed
         with the client on 2026-08-01"}

    A blank reason is refused by :func:`gates.waive`. A key matching no gate is
    refused here, because a typo would silently leave the gate unwaived.
    """
    try:
        raw = json.loads(path.read_text())
    except OSError as exc:
        raise typer.BadParameter(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise typer.BadParameter(
            f"{path}: expected an object mapping gate to the reason it was waived"
        )
    waivers: dict[str, str] = {}
    for key, reason in raw.items():
        if not isinstance(reason, str) or not reason.strip():
            raise typer.BadParameter(
                f"{path}: {key!r} needs a non-empty reason. A waiver with no "
                "reason is indistinguishable from a gate somebody deleted."
            )
        waivers[str(key)] = reason
    return waivers


def _apply_waivers(results: list, waivers: dict[str, str]) -> tuple[list, list[str]]:
    """Waive matching gates, returning the results and any key that matched none."""
    if not waivers:
        return results, []
    matched: set[str] = set()
    applied = []
    for result in results:
        keys = [result.gate]
        if result.subject:
            keys.append(f"{result.gate}/{result.subject}")
        # Most specific first, so a subject-scoped waiver beats a gate-wide one.
        hit = next((k for k in reversed(keys) if k in waivers), None)
        if hit is None:
            applied.append(result)
            continue
        matched.add(hit)
        applied.append(gates_waive(result, reason=waivers[hit]))
    return applied, sorted(set(waivers) - matched)


def _capacity_for(tests: list[dict], plan: dict | None = None) -> dict:
    """Derive the calls-per-node figure and the node count at each tier.

    The derivation is the one number the whole table rests on, and it refuses
    far more often than it answers. Its REASON is returned either way: a report
    saying why it could not size the fleet is worth more than one quietly
    missing the section, and far more than one carrying a number nobody earned.

    The ceiling is imported from the gates rather than typed here. It is a
    contracted threshold and two copies of it would drift.
    """
    # Arrival rate and hold live in the generator's scenario file, which is not
    # an artifact, so they arrive only when an operator declared them. Absent,
    # the plan-side plateau check cannot run, which the derivation accounts for
    # rather than treating as a clean ramp.
    declared = plan or {}
    steps = [
        RampStep(
            target_concurrency=test.get("target_concurrency"),
            peak_concurrency=test.get("peak_concurrency"),
            peak_cpu_utilisation=test.get("peak_cpu_utilisation"),
            rate_per_second=declared.get(test.get("name"), {}).get("rate_per_second"),
            hold_seconds=declared.get(test.get("name"), {}).get("hold_seconds"),
        )
        for test in tests
    ]
    calls_per_node, reason = derive_calls_per_node(
        steps, cpu_ceiling=MAX_NODE_CPU_UTILISATION
    )
    capacity: dict = {"calls_per_node": calls_per_node, "reason": reason}
    # Label what this rests on. A target concurrency is an operator ASSERTION
    # about what the generator was asked for; nothing measured it, and the whole
    # table is derived from steps carrying one. A reader deciding how much
    # weight to put on the sizing needs to know that, and a payload that looks
    # identical whether the figures came from artifacts or from a declaration
    # cannot tell them.
    steps_declared = sorted(
        str(test.get("name"))
        for test in tests
        if test.get("target_concurrency") is not None
    )
    if steps_declared:
        capacity["rests_on_declaration"] = True
        capacity["declared_steps"] = steps_declared
        capacity["declaration_basis"] = (
            "target concurrency was declared by the operator at import, not "
            "measured. The peak each step REACHED is measured and is not "
            "overwritten by it; a declared target the run did not reach refuses "
            "the derivation rather than sizing from it."
        )
    if calls_per_node is None:
        # Refused. The reason is the payload, and no tiers are invented.
        return capacity
    capacity["tiers"] = [
        {
            "target_concurrency": tier.target_concurrency,
            "calls_per_node": tier.calls_per_node,
            "usable_per_node": tier.usable_per_node,
            "nodes_for_load": tier.nodes_for_load,
            "spare_nodes": tier.spare_nodes,
            "nodes": tier.nodes,
        }
        for tier in capacity_table(calls_per_node)
    ]
    # instance_type is deliberately absent. Nothing here can derive a machine
    # type, and InstanceType requires a citation for exactly that reason, so it
    # is supplied by whoever holds the source rather than guessed at here.
    return capacity


#: The sections _render_appendix renders, in the order it renders them. A file
#: naming anything else is refused rather than partially read: a typo would
#: otherwise drop a whole section silently, and the operator would hand over a
#: report missing the commands that produced it.
_APPENDIX_SECTIONS = ("commands", "flags", "toolchain")


def _appendix_from_file(path: Path) -> dict[str, list[dict[str, str]]]:
    """Read an operator-supplied appendix, refusing anything uncited.

    The entries live in a file the operator holds rather than in this
    repository, for two reasons. The commands belong to a particular engagement
    and have no business being compiled into a general tool. And the generator
    they drive is AGPL-3.0 while this repository is MIT, so its scenarios and
    configuration must not be copied here; flag names and command lines are
    interface facts and travel fine.

    Every entry goes through :func:`appendix_entry`, which requires a citation
    and reduces any absolute URL to a bare host. An uncited command is
    indistinguishable from one this report invented, so it is refused by label
    rather than dropped quietly.

    Shape::

        {"commands": [{"label": ..., "detail": ..., "citation": ...}, ...],
         "flags":    [...],
         "toolchain": [...]}
    """
    try:
        raw = json.loads(path.read_text())
    except OSError as exc:
        raise typer.BadParameter(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise typer.BadParameter(f"{path}: expected an object of sections")

    unknown = sorted(set(raw) - set(_APPENDIX_SECTIONS))
    if unknown:
        raise typer.BadParameter(
            f"{path}: unknown section(s) {unknown}. Known sections are "
            f"{list(_APPENDIX_SECTIONS)}; a misspelled one would silently drop "
            "every entry under it."
        )

    out: dict[str, list[dict[str, str]]] = {}
    for section in _APPENDIX_SECTIONS:
        entries = raw.get(section) or []
        if not isinstance(entries, list):
            raise typer.BadParameter(f"{path}: {section} must be a list")
        built: list[dict[str, str]] = []
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise typer.BadParameter(
                    f"{path}: {section}[{index}] must be an object"
                )
            try:
                built.append(
                    appendix_entry(
                        label=str(entry.get("label", "")),
                        detail=str(entry.get("detail", "")),
                        citation=str(entry.get("citation", "")),
                    )
                )
            except ValueError as exc:
                # Refused by label so the operator can find the offending line.
                raise typer.BadParameter(f"{path}: {section}[{index}]: {exc}") from exc
        if built:
            out[section] = built
    return out


@loadtest_app.command("report")
def report(
    run_id: str = typer.Argument(..., help="Run id to report on"),
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
    out: Path = typer.Option(
        Path(".artifacts/capacity-evidence"),
        "--out",
        "-o",
        help="Directory to write the report into",
    ),
    appendix: Path = typer.Option(
        None,
        "--appendix",
        help=(
            "JSON file of reproducible-test assets. Sections: commands, flags, "
            "toolchain. Every entry needs label, detail and a citation."
        ),
    ),
    waive: Path = typer.Option(
        None,
        "--waive",
        help=(
            "JSON mapping gate (or gate/subject) to the reason it was waived in "
            "writing. Records a threshold the run was not held to; never turns "
            "it into a pass, and the report still exits non-zero."
        ),
    ),
) -> None:
    """Write one run's report as JSON plus a self-contained HTML file."""
    from voicegateway.livekit_diag.run_report import (
        build_load_payload,
        load_report_filename,
        render_load_html,
    )

    gw = _cli.require_gateway(config)
    storage = _cli.require_storage(gw)

    run = _cli.async_run(storage.get_load_run(run_id))
    if run is None:
        _cli.fail(f"No load run {run_id!r}. Import one first.", code=2)
    tests = _cli.async_run(storage.list_load_run_tests(run_id))

    # Judge before rendering. A report that carries numbers and no verdict
    # cannot answer the question it exists to answer, and the gates were
    # previously reachable from nothing.
    aggregates = {}
    for test in tests:
        aggregates[test["name"]] = _cli.async_run(
            storage.correlate_load_run_test(
                started_at_ms=test.get("started_at_ms"),
                ended_at_ms=test.get("ended_at_ms"),
            )
        )
    # Declared at import and carried in the run notes, exactly as the ramp plan
    # is. A node with no declaration reaches the bandwidth gate as UNKNOWN: the
    # instance type is never read to infer a baseline, because a guessed
    # denominator would look identical to a measured one in the report.
    results = judge_run(
        tests,
        aggregates=aggregates,
        network_baselines=network_baselines_from_notes(run.get("notes")),
    )
    results, unmatched = _apply_waivers(
        results, _waivers_from_file(waive) if waive is not None else {}
    )
    if unmatched:
        _cli.fail(
            f"--waive names {unmatched} which match no gate in this run. Gates "
            f"here: {sorted({g.gate for g in results})}. A typo would otherwise "
            "leave the gate unwaived and the waiver unrecorded.",
            code=2,
        )
    capacity = _capacity_for(tests, plan_from_notes(run.get("notes")))
    assets = _appendix_from_file(appendix) if appendix is not None else None
    payload = build_load_payload(
        run=run,
        tests=tests,
        gate_results=[g.as_dict() for g in results],
        capacity=capacity,
        appendix=assets,
        # Recovered from the row rather than recomputed: the artifacts may be
        # long gone by the time somebody exports the report, and what the import
        # could not answer is a property of that import.
        limitations=limitations_from_notes(run.get("notes")),
        # Derived from what nothing can measure, never from a list of names, so
        # wiring an exporter for them lifts the exclusion by itself.
        scope_exclusions=excluded_headroom_resources(),
    )
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"{load_report_filename(run_id).removesuffix('.html')}.json"
    html_path = out / load_report_filename(run_id)
    json_path.write_text(json.dumps(payload, indent=2) + "\n")
    html_path.write_text(render_load_html(payload))

    # Read from the payload rather than recomputed, so the console, the exit
    # code, the JSON and the HTML are one derivation rather than four that
    # happen to agree today.
    run_verdict = str(payload["verdict"]["status"])
    counts: dict[str, int] = {}
    for gate in results:
        counts[gate.status] = counts.get(gate.status, 0) + 1
    breakdown = ", ".join(f"{n} {status}" for status, n in sorted(counts.items()))
    console.print(f"\nVerdict: [bold]{run_verdict}[/bold]  ({breakdown})")
    if run_verdict == "UNKNOWN":
        _cli.warn(
            "UNKNOWN is not a pass. At least one acceptance criterion could not "
            "be evaluated; the run did not demonstrate it, it failed to measure it."
        )

    if capacity.get("calls_per_node") is None:
        _cli.warn(f"No capacity table: {capacity['reason']}")
    else:
        console.print(
            f"Sized from {capacity['calls_per_node']} calls per node: "
            + ", ".join(
                f"{t['target_concurrency']}->{t['nodes']} nodes"
                for t in capacity["tiers"]
            )
        )

    if assets is None:
        _cli.warn(
            "No reproducible-test assets recorded. Without the commands and "
            "flag semantics behind them these numbers cannot be reproduced by "
            "anybody else, which is most of what makes them evidence. Supply "
            "them with --appendix."
        )
    else:
        console.print(
            "Appendix: " + ", ".join(f"{len(v)} {k}" for k, v in sorted(assets.items()))
        )

    if payload["data_provenance"] != "measured":
        _cli.warn(
            "SYNTHETIC: the HTML carries the not-a-deliverable stamp as its "
            "first visible element. Replace the fixtures with captured "
            "artifacts and re-import with --captured before handing it over."
        )
    _cli.success(f"Wrote {json_path} and {html_path}")

    # Exit AFTER both files are written. A failing run is exactly the run whose
    # evidence somebody needs, so exiting before writing it would destroy the
    # only record of why it failed.
    #
    # The code comes from gates.exit_code rather than a second convention here:
    # 0 only for PASS. WAIVED is not clean (a waived gate is one nobody held the
    # run to, and a pipeline that goes green on a waiver has dropped the
    # requirement rather than recorded it) and UNKNOWN is not clean either (the
    # run failed to measure something, which is not the same as demonstrating
    # it). One non-zero code on purpose, so an existing `if [ $? -eq 1 ]` keeps
    # catching every kind of failure.
    code = exit_code(run_verdict)
    if code != 0:
        raise typer.Exit(code)
