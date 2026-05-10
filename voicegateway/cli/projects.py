"""``voicegw projects`` and (later) ``voicegw project`` commands.

Carved out of voicegateway/cli/_legacy.py during the v0.1.0 section-2
refactor. This module owns the project-related Typer commands. The
listing command (``projects``) lands first; the detail command
(``project``) lands in the next refactor commit.
"""

from __future__ import annotations

import typer
from rich.table import Table

# See voicegateway/cli/init.py for the rationale on importing
# ``app`` and ``console`` from ``_legacy`` rather than from the
# package during the v0.1.0 migration period.
from voicegateway.cli._legacy import _load_gateway, app, console


@app.command(name="projects")
def projects_cmd(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
) -> None:
    """List all configured projects."""
    gw = _load_gateway(config)

    if not gw.config.projects:
        console.print(
            "[yellow]No projects configured. Add a 'projects:' section to voicegw.yaml.[/yellow]"
        )
        raise typer.Exit(0)

    table = Table(title="Projects")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Tags")
    table.add_column("Budget/day", style="green", justify="right")
    table.add_column("Default Stack")

    for pid, pcfg in sorted(gw.config.projects.items()):
        tags = " ".join(f"[bold]{t}[/bold]" for t in pcfg.tags)
        budget = f"${pcfg.daily_budget:.2f}" if pcfg.daily_budget else "-"
        table.add_row(pid, pcfg.name, tags, budget, pcfg.default_stack or "-")

    console.print(table)
