"""``voicegw logs`` command.

Carved out of voicegateway/cli/_legacy.py during the v0.1.0 section-2
refactor. Prints the most recent N request rows from the SQLite
storage backend, optionally filtered by project or modality.
"""

from __future__ import annotations

import asyncio
import datetime

import typer
from rich.table import Table

# See voicegateway/cli/init.py for the rationale on importing
# ``app`` and ``console`` from ``_legacy`` rather than from the
# package during the v0.1.0 migration period.
from voicegateway.cli._legacy import _load_gateway, app, console


@app.command(name="logs")
def logs_cmd(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
    project: str = typer.Option(None, "--project", "-p", help="Filter by project"),
    tail: int = typer.Option(20, "--tail", "-n", help="Number of rows"),
    modality: str = typer.Option(None, "--modality", "-m", help="stt, llm, or tts"),
) -> None:
    """Show recent request logs."""
    gw = _load_gateway(config)
    if gw.storage is None:
        console.print("[yellow]Cost tracking is not enabled in voicegw.yaml[/yellow]")
        raise typer.Exit(0)

    rows = asyncio.run(
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
