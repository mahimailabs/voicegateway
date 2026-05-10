"""``voicegw costs`` command.

Carved out of voicegateway/cli/_legacy.py during the v0.1.0 section-2
refactor. Prints today/week/month cost summary plus the modality-aware
pricing-source attribution line.
"""

from __future__ import annotations

import asyncio

import typer
from rich.table import Table

# See voicegateway/cli/init.py for the rationale on importing
# ``app`` and ``console`` from ``_legacy`` rather than from the
# package during the v0.1.0 migration period.
from voicegateway.cli._legacy import _load_gateway, app, console


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

    # Cost-staleness reminder (Q7). Print as a single dim-styled line
    # so terminals without color support still get the message. Naming
    # the catalogs surfaces the modality-aware unit accounting story
    # without forcing the reader to remember which prices come from
    # genai-prices and which from VG's local catalog.
    from voicegateway.pricing import llm as _llm_pricing
    from voicegateway.pricing import stt as _stt_pricing
    from voicegateway.pricing import tts as _tts_pricing

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
