"""``voicegw doctor`` command."""

from __future__ import annotations

import typer
from rich.table import Table

from voicegateway.cli._app import app, console
from voicegateway.core.constants import STATUS_RENDER
from voicegateway.utils.cli.doctor import _CHECKS, _build_context


@app.command()
def doctor(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
) -> None:
    """Run diagnostic checks. Numbered punch list with fix actions."""
    ctx = _build_context(config)
    results = [check(ctx) for check in _CHECKS]

    table = Table(title="VoiceGateway doctor")
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("Check", style="cyan")
    table.add_column("Status", width=6)
    table.add_column("Detail", overflow="fold")

    for i, r in enumerate(results, start=1):
        table.add_row(
            str(i),
            r.label,
            STATUS_RENDER.get(r.status, r.status.upper()),
            r.detail,
        )

    console.print(table)

    failures = [r for r in results if r.is_blocker]
    if failures:
        labels = ", ".join(r.label for r in failures)
        console.print(
            f"\n[yellow]{len(failures)} check(s) need attention:[/yellow] {labels}"
        )
        raise typer.Exit(1)

    console.print("\n[green]All checks passed.[/green]")
