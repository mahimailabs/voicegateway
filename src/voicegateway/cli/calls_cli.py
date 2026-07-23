"""``voicegw calls`` command: cost + activity per call, the unit an operator
actually thinks in (a call/session), not per model request."""

from __future__ import annotations

from datetime import datetime

import typer
from rich.table import Table

from voicegateway.cli._app import app, console
from voicegateway.cli.base_cli import BaseCli

_cli = BaseCli()

_SORTS = {"recent": "started_at_desc", "cost": "cost_desc"}


def _fmt_started(value: object) -> str:
    """Format an ISO started_at into a short ``MM-DD HH:MM``; pass through junk."""
    if not isinstance(value, str) or not value:
        return "-"
    try:
        return datetime.fromisoformat(value).strftime("%m-%d %H:%M")
    except ValueError:
        return value[:16]


@app.command()
def calls(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
    project: str = typer.Option(None, "--project", "-p", help="Filter by project ID"),
    limit: int = typer.Option(20, "--limit", "-n", help="How many calls to show"),
    sort: str = typer.Option(
        "recent", "--sort", "-s", help="Order: recent (default) or cost"
    ),
) -> None:
    """List recent calls with per-call cost and activity."""
    gw = _cli.require_gateway(config)
    if gw.storage is None:
        _cli.warn("Cost tracking is not enabled in voicegw.yaml")
        raise typer.Exit(0)

    order_by = _SORTS.get(sort)
    if order_by is None:
        _cli.fail(f"Unknown sort: {sort}. Use recent or cost.", code=2)

    rows = _cli.async_run(
        gw.storage.list_sessions(limit=limit, project=project, order_by=order_by)
    )
    if not rows:
        console.print("[dim]No calls recorded yet.[/dim]")
        raise typer.Exit(0)

    table = Table(title=f"Recent Calls ({len(rows)})")
    table.add_column("Call", style="cyan")
    table.add_column("Started")
    table.add_column("Modalities")
    table.add_column("Requests", justify="right")
    table.add_column("Cost", style="green", justify="right")
    total = 0.0
    for r in rows:
        cost = float(r.get("total_cost_usd") or 0.0)
        total += cost
        modalities = r.get("modalities") or []
        table.add_row(
            str(r.get("id", "-")),
            _fmt_started(r.get("started_at")),
            "·".join(modalities) if isinstance(modalities, list) else str(modalities),
            str(int(r.get("request_count") or 0)),
            f"${cost:.4f}",
        )
    console.print(table)

    avg = total / len(rows) if rows else 0.0
    console.print(
        f"\n[dim]{len(rows)} calls · ${total:.4f} total · ${avg:.4f} avg/call[/dim]"
    )
