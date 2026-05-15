"""``voicegw logs`` command."""

from __future__ import annotations

import datetime

import typer
from rich.table import Table

from voicegateway.cli._app import app, console
from voicegateway.cli.base_cli import BaseCli

_cli = BaseCli()


@app.command(name="logs")
def logs_cmd(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
    project: str = typer.Option(None, "--project", "-p", help="Filter by project"),
    tail: int = typer.Option(20, "--tail", "-n", help="Number of rows"),
    modality: str = typer.Option(None, "--modality", "-m", help="stt, llm, or tts"),
) -> None:
    """Show recent request logs."""
    gw = _cli.require_gateway(config)
    if gw.storage is None:
        _cli.warn("Cost tracking is not enabled in voicegw.yaml")
        raise typer.Exit(0)

    rows = _cli.async_run(
        gw.storage.get_recent_requests(limit=tail, modality=modality, project=project)
    )
    if not rows:
        console.print("[dim]No logs found.[/dim]")
        return

    table = Table(title=f"Recent Requests ({len(rows)})")
    table.add_column("Time", style="cyan")
    table.add_column("Project", style="magenta")
    table.add_column("Modality")
    table.add_column("Model")
    table.add_column("Cost", justify="right")
    table.add_column("Latency", justify="right")
    table.add_column("Status")

    for r in rows:
        ts = datetime.datetime.fromtimestamp(r["timestamp"]).strftime("%H:%M:%S")
        table.add_row(
            ts,
            r.get("project") or "-",
            r.get("modality", "").upper(),
            r.get("model_id", ""),
            f"${r.get('cost_usd', 0):.6f}",
            f"{int(r.get('total_latency_ms') or 0)}ms",
            r.get("status", ""),
        )
    console.print(table)
