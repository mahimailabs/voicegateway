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
from voicegateway.loadtest.aggregation import TestAggregate, peak_label
from voicegateway.loadtest.artifacts import ArtifactError
from voicegateway.loadtest.importer import build_plan, observations_for

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
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be written, write nothing"
    ),
) -> None:
    """Import one directory of artifacts as a run with a row per test."""
    try:
        plan = build_plan(
            directory,
            run_id=run_id,
            project=project,
            label=label,
            captured=captured,
            now_ms=int(time.time() * 1000),
        )
    except ArtifactError as exc:
        # Named errors carry which surface disagreed and how, so they are shown
        # rather than flattened into a generic import failure.
        _cli.fail(f"{type(exc).__name__}: {exc}", code=2)

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

    payload = build_load_payload(run=run, tests=tests)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"{load_report_filename(run_id).removesuffix('.html')}.json"
    html_path = out / load_report_filename(run_id)
    json_path.write_text(json.dumps(payload, indent=2) + "\n")
    html_path.write_text(render_load_html(payload))

    if payload["data_provenance"] != "measured":
        _cli.warn(
            "SYNTHETIC: the HTML carries the not-a-deliverable stamp as its "
            "first visible element. Replace the fixtures with captured "
            "artifacts and re-import with --captured before handing it over."
        )
    _cli.success(f"Wrote {json_path} and {html_path}")
