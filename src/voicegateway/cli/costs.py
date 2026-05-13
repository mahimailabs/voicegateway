"""``voicegw costs`` command."""

from __future__ import annotations

import asyncio

import typer
from rich.table import Table

from voicegateway.cli._app import app, console
from voicegateway.utils.cli._shared import _load_gateway


@app.command()
def costs(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
    project: str = typer.Option(None, "--project", "-p", help="Filter by project ID"),
    week: bool = typer.Option(False, "--week", help="Show weekly summary"),
    month: bool = typer.Option(False, "--month", help="Show monthly summary"),
) -> None:
    """Show cost summary."""
    gw = _load_gateway(config)
    period = "month" if month else ("week" if week else "today")

    if gw.storage is None:
        console.print("[yellow]Cost tracking is not enabled in voicegw.yaml[/yellow]")
        raise typer.Exit(0)

    summary = asyncio.run(gw.storage.get_cost_summary(period, project=project))

    header = f"Cost Summary ({period})"
    if project:
        header += f" — project: {project}"
    console.print(f"\n[bold]{header}[/bold]")
    console.print(f"Total: [green]${summary['total']:.4f}[/green]\n")

    if summary["by_provider"]:
        table = Table(title="By Provider")
        table.add_column("Provider", style="cyan")
        table.add_column("Cost", style="green", justify="right")
        table.add_column("Requests", justify="right")
        for provider_name, data in summary["by_provider"].items():
            table.add_row(provider_name, f"${data['cost']:.4f}", str(data["requests"]))
        console.print(table)

    if summary["by_model"]:
        table = Table(title="By Model")
        table.add_column("Model", style="cyan")
        table.add_column("Cost", style="green", justify="right")
        table.add_column("Requests", justify="right")
        for model, data in summary["by_model"].items():
            table.add_row(model, f"${data['cost']:.4f}", str(data["requests"]))
        console.print(table)

    if not summary["by_provider"]:
        console.print("[dim]No requests recorded yet.[/dim]")

    from voicegateway.inference.pricing import llm as _llm_pricing
    from voicegateway.inference.pricing import stt as _stt_pricing
    from voicegateway.inference.pricing import tts as _tts_pricing

    sources = (
        f"LLM: {_llm_pricing.PRICING_SOURCE} | "
        f"STT: {_stt_pricing.PRICING_SOURCE} | "
        f"TTS: {_tts_pricing.PRICING_SOURCE}"
    )
    console.print(f"\n[dim]Pricing sources: {sources}[/dim]")
    console.print(
        "[dim]Costs are estimates. Run "
        "`voicegw reconcile --provider <name> "
        "--provider-usage-file <file>` to verify against your "
        "provider invoice.[/dim]"
    )
