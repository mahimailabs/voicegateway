"""CLI for VoiceGateway."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

app = typer.Typer(
    name="voicegw",
    help="VoiceGateway — self-hosted inference gateway for voice AI",
    no_args_is_help=True,
)
console = Console()

# The example config ships next to the package. Try both the new and legacy names.
_PACKAGE_ROOT = Path(__file__).parent.parent
_EXAMPLE_CONFIG_CANDIDATES = [
    _PACKAGE_ROOT / "voicegw.example.yaml",
    _PACKAGE_ROOT / "gateway.example.yaml",
]


def _find_example_config() -> Path | None:
    for p in _EXAMPLE_CONFIG_CANDIDATES:
        if p.exists():
            return p
    return None


def _load_gateway(config_path: str | None):
    from voicegateway import Gateway
    try:
        return Gateway(config_path=config_path)
    except Exception as e:
        console.print(f"[red]Error loading config: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def init(
    output: str = typer.Option(
        "./voicegw.yaml", "--output", "-o", help="Output path for config file"
    ),
):
    """Create a voicegw.yaml configuration file."""
    dest = Path(output)
    if dest.exists():
        overwrite = typer.confirm(f"{dest} already exists. Overwrite?")
        if not overwrite:
            raise typer.Abort()

    example = _find_example_config()
    if example is not None:
        shutil.copy(example, dest)
    else:
        dest.write_text(
            "# VoiceGateway Configuration\n"
            "# See: https://github.com/mahimailabs/voicegateway\n\n"
            "providers: {}\nmodels:\n  stt: {}\n  llm: {}\n  tts: {}\n"
            "projects: {}\n"
        )

    console.print(f"[green]Created {dest}[/green]")
    console.print("Edit it with your API keys, models, and projects.")


@app.command()
def status(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
    project: str = typer.Option(None, "--project", "-p", help="Filter by project ID"),
):
    """Show provider status."""
    gw = _load_gateway(config)
    cfg = gw.config

    if project and cfg.projects and project not in cfg.projects:
        console.print(f"[red]Unknown project: {project}[/red]")
        raise typer.Exit(1)

    table = Table(title=f"Provider Status{f' — {project}' if project else ''}")
    table.add_column("Provider", style="cyan")
    table.add_column("Configured", style="green")
    table.add_column("Models")

    for provider_name, provider_config in sorted(cfg.providers.items()):
        has_key = bool(provider_config.get("api_key")) or provider_name in (
            "ollama", "whisper", "kokoro", "piper"
        )
        model_count = 0
        for modality_models in cfg.models.values():
            if isinstance(modality_models, dict):
                for model_cfg in modality_models.values():
                    if isinstance(model_cfg, dict) and model_cfg.get("provider") == provider_name:
                        model_count += 1
        status_str = "[green]Yes[/green]" if has_key else "[red]No API key[/red]"
        table.add_row(provider_name, status_str, str(model_count))

    console.print(table)


@app.command()
def costs(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
    project: str = typer.Option(None, "--project", "-p", help="Filter by project ID"),
    week: bool = typer.Option(False, "--week", help="Show weekly summary"),
    month: bool = typer.Option(False, "--month", help="Show monthly summary"),
):
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


@app.command(name="projects")
def projects_cmd(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
):
    """List all configured projects."""
    gw = _load_gateway(config)

    if not gw.config.projects:
        console.print("[yellow]No projects configured. Add a 'projects:' section to voicegw.yaml.[/yellow]")
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


@app.command(name="project")
def project_cmd(
    project_id: str = typer.Argument(..., help="Project ID to show"),
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
):
    """Show details for a single project."""
    gw = _load_gateway(config)
    pcfg = gw.config.get_project(project_id)

    if pcfg is None:
        console.print(f"[red]Project not found: {project_id}[/red]")
        raise typer.Exit(1)

    body = (
        f"[bold]{pcfg.name}[/bold]\n"
        f"{pcfg.description or '(no description)'}\n\n"
        f"Tags: {', '.join(pcfg.tags) or '-'}\n"
        f"Default Stack: {pcfg.default_stack or '-'}\n"
        f"Daily Budget: ${pcfg.daily_budget:.2f}"
    )
    console.print(Panel(body, title=f"Project: {project_id}", border_style="cyan"))

    if gw.storage is not None:
        today = asyncio.run(gw.storage.get_cost_summary("today", project=project_id))
        console.print(f"\n[bold]Today[/bold]: ${today['total']:.4f} "
                      f"({sum(v['requests'] for v in today['by_provider'].values())} requests)")


@app.command(name="logs")
def logs_cmd(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
    project: str = typer.Option(None, "--project", "-p", help="Filter by project"),
    tail: int = typer.Option(20, "--tail", "-n", help="Number of rows"),
    modality: str = typer.Option(None, "--modality", "-m", help="stt, llm, or tts"),
):
    """Show recent request logs."""
    gw = _load_gateway(config)
    if gw.storage is None:
        console.print("[yellow]Cost tracking is not enabled in voicegw.yaml[/yellow]")
        raise typer.Exit(0)

    rows = asyncio.run(gw.storage.get_recent_requests(
        limit=tail, modality=modality, project=project
    ))
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

    import datetime
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


@app.command(name="serve")
def serve_cmd(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
    host: str = typer.Option("0.0.0.0", "--host", help="Bind host"),
    port: int = typer.Option(8080, "--port", help="Bind port"),
):
    """Start the VoiceGateway HTTP API server."""
    try:
        import uvicorn
    except ImportError:
        console.print(
            "[red]Dashboard dependencies not installed. "
            "Run: pip install 'voicegateway[dashboard]'[/red]"
        )
        raise typer.Exit(1)

    gw = _load_gateway(config)
    from voicegateway.server import build_app
    api_app = build_app(gw)
    console.print(f"[green]VoiceGateway API starting at http://{host}:{port}[/green]")
    uvicorn.run(api_app, host=host, port=port)


@app.command(name="dashboard")
def dashboard_cmd(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
    host: str = typer.Option("0.0.0.0", "--host", help="Dashboard host"),
    port: int = typer.Option(9090, "--port", help="Dashboard port"),
):
    """Start the web dashboard."""
    try:
        import uvicorn
    except ImportError:
        console.print(
            "[red]Dashboard dependencies not installed. "
            "Run: pip install 'voicegateway[dashboard]'[/red]"
        )
        raise typer.Exit(1)

    gw = _load_gateway(config)
    console.print(f"[green]VoiceGateway dashboard at http://{host}:{port}[/green]")

    import dashboard.api.main as dashboard_app
    dashboard_app._gateway = gw
    uvicorn.run(dashboard_app.app, host=host, port=port)


if __name__ == "__main__":
    app()
