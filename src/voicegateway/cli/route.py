"""``voicegw route`` command group: routing observations + dry-run simulator.

Read-only ops surface for cross-modality routing (REQ-VG-ROUTE-002 +
-003). Two subcommands:

* ``voicegw route show <project>`` — prints the current
  ``latency_observations`` rollup rows for a project plus the project's
  rosters from voicegw.yaml. Useful when debugging \"why did the router
  pick X\".
* ``voicegw route simulate <project> [--stt X] [--llm Y] [--tts Z]`` —
  runs ``route_session`` against the live observations and the project
  config (no session row written). Prints the picked triple, the
  predicted total, and the budget_overrun flag.

Issuance / config edits are NOT in scope here. The CLI is read-only;
operators tune budgets and rosters via voicegw.yaml.

Path correction note: Foundry text listed ``voicegw/cli/route.py``;
the actual CLI subpackage is ``voicegateway/cli/`` (same correction
as v0.3.0 / v0.4.0).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from typing import Any

import typer

from voicegateway.cli._app import app, console
from voicegateway.cli._helpers import _load_gateway

route_app = typer.Typer(
    name="route",
    help="Read-only routing diagnostics: show observations + simulate triple pick.",
    no_args_is_help=True,
)
app.add_typer(route_app, name="route")


async def _show_async(storage: Any, project_id: str) -> list[Any]:
    from voicegateway.storage import latency_observations_repo

    db = await storage._ensure_initialized()
    return await latency_observations_repo.get_for_project(db, project_id)


async def _simulate_async(
    storage: Any,
    *,
    project_id: str,
    project_config: Any,
    overrides: dict[str, str],
) -> Any:
    from voicegateway.middleware import router

    db = await storage._ensure_initialized()
    return await router.route_session(
        db,
        project_id=project_id,
        project_config=project_config,
        caller_overrides=overrides or None,
    )


@route_app.command("show")
def show_cmd(
    project: str = typer.Argument(..., help="Project id to inspect."),
    config: str | None = typer.Option(
        None, "--config", "-c", help="Path to voicegw.yaml."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit JSON instead of the rendered table."
    ),
) -> None:
    """Show the latency_observations rollup + rosters for a project."""
    gw = _load_gateway(config)
    if gw.storage is None:
        console.print(
            "[red]Storage backend not configured (cost_tracking.db_path).[/red]"
        )
        raise typer.Exit(1)

    rows = asyncio.run(_show_async(gw.storage, project))
    project_cfg = gw.config.projects.get(project)
    rosters = (
        getattr(getattr(project_cfg, "routing", None), "rosters", {}) or {}
        if project_cfg is not None
        else {}
    )
    budget = (
        getattr(getattr(project_cfg, "routing", None), "budget_ms", 1500)
        if project_cfg is not None
        else 1500
    )

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "project_id": project,
                    "budget_ms": budget,
                    "rosters": rosters,
                    "observations": [dataclasses.asdict(r) for r in rows],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    console.print(f"[bold]Project {project}[/bold]  budget_ms={budget}")
    if rosters:
        console.print("\n[bold]Rosters[/bold]")
        for modality in ("stt", "llm", "tts"):
            providers = rosters.get(modality) or []
            console.print(f"  {modality:<5} {', '.join(providers) or '(empty)'}")
    else:
        console.print("[dim](no routing rosters configured)[/dim]")

    if not rows:
        console.print(
            "\n[dim]No latency observations yet. The worker rolls up "
            "every 15 minutes once sessions complete.[/dim]"
        )
        return

    console.print("\n[bold]Observations[/bold]")
    console.print(
        f"{'MODALITY':<10} {'PROVIDER':<14} {'P50 MS':>8} {'P95 MS':>8} {'SAMPLES':>9}"
    )
    console.print("-" * 56)
    for r in rows:
        p50 = "-" if r.p50_ms is None else str(r.p50_ms)
        p95 = "-" if r.p95_ms is None else str(r.p95_ms)
        console.print(
            f"{r.modality:<10} {r.provider:<14} {p50:>8} {p95:>8} {r.sample_count:>9}"
        )


@route_app.command("simulate")
def simulate_cmd(
    project: str = typer.Argument(..., help="Project id to simulate against."),
    stt: str | None = typer.Option(
        None, "--stt", help="Override the STT provider pick."
    ),
    llm: str | None = typer.Option(
        None, "--llm", help="Override the LLM provider pick."
    ),
    tts: str | None = typer.Option(
        None, "--tts", help="Override the TTS provider pick."
    ),
    config: str | None = typer.Option(
        None, "--config", "-c", help="Path to voicegw.yaml."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit JSON instead of the rendered triple."
    ),
) -> None:
    """Dry-run the router with optional per-modality overrides."""
    gw = _load_gateway(config)
    if gw.storage is None:
        console.print(
            "[red]Storage backend not configured (cost_tracking.db_path).[/red]"
        )
        raise typer.Exit(1)
    project_cfg = gw.config.projects.get(project)
    if project_cfg is None:
        console.print(f"[red]Project {project!r} is not in voicegw.yaml.[/red]")
        raise typer.Exit(1)

    overrides: dict[str, str] = {}
    if stt:
        overrides["stt"] = stt
    if llm:
        overrides["llm"] = llm
    if tts:
        overrides["tts"] = tts

    try:
        from voicegateway.middleware.router import BudgetExceeded

        triple = asyncio.run(
            _simulate_async(
                gw.storage,
                project_id=project,
                project_config=project_cfg,
                overrides=overrides,
            )
        )
    except BudgetExceeded as exc:
        console.print(f"[red]BudgetExceeded:[/red] {exc}")
        raise typer.Exit(1) from exc
    except ValueError as exc:
        console.print(f"[red]Router rejected the call:[/red] {exc}")
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(dataclasses.asdict(triple), indent=2))
        return

    console.print(f"[bold]Project {project}[/bold] simulated route:")
    console.print(f"  STT: {triple.stt}")
    console.print(f"  LLM: {triple.llm}")
    console.print(f"  TTS: {triple.tts}")
    console.print(f"  Predicted total: {triple.predicted_ms} ms")
    if triple.budget_overrun:
        console.print("  [yellow]budget_overrun = True (fastest fallback)[/yellow]")
    else:
        budget = project_cfg.routing.budget_ms
        console.print(f"  Under budget ({budget} ms): yes")


__all__ = ["route_app", "show_cmd", "simulate_cmd"]
