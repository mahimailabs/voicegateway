"""CLI for VoiceGateway."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

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


def _version_callback(value: bool) -> None:
    """Print the package version and exit (eager option)."""
    if value:
        from voicegateway import __version__

        console.print(__version__)
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the VoiceGateway version and exit.",
    ),
) -> None:
    """Root callback hosting global options like --version."""

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
        raise typer.Exit(1) from e


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
            "ollama",
            "whisper",
            "kokoro",
            "piper",
        )
        model_count = 0
        for modality_models in cfg.models.values():
            if isinstance(modality_models, dict):
                for model_cfg in modality_models.values():
                    if (
                        isinstance(model_cfg, dict)
                        and model_cfg.get("provider") == provider_name
                    ):
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
        console.print(
            f"\n[bold]Today[/bold]: ${today['total']:.4f} "
            f"({sum(v['requests'] for v in today['by_provider'].values())} requests)"
        )


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
    except ImportError as e:
        console.print(
            "[red]Dashboard dependencies not installed. "
            "Run: pip install 'voicegateway[dashboard]'[/red]"
        )
        raise typer.Exit(1) from e

    gw = _load_gateway(config)
    from voicegateway.core.auth import describe_auth, load_api_keys
    from voicegateway.server import build_app

    api_app = build_app(gw)
    console.print(f"[green]VoiceGateway API starting at http://{host}:{port}[/green]")
    console.print(f"[cyan]{describe_auth(load_api_keys(gw.config.auth))}[/cyan]")
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
    except ImportError as e:
        console.print(
            "[red]Dashboard dependencies not installed. "
            "Run: pip install 'voicegateway[dashboard]'[/red]"
        )
        raise typer.Exit(1) from e

    gw = _load_gateway(config)
    from voicegateway.core.auth import describe_auth, load_api_keys

    console.print(f"[green]VoiceGateway dashboard at http://{host}:{port}[/green]")
    console.print(f"[cyan]{describe_auth(load_api_keys(gw.config.auth))}[/cyan]")

    import dashboard.api.main as dashboard_app

    dashboard_app.configure(gw)
    uvicorn.run(dashboard_app.app, host=host, port=port)


_EXPORT_COLUMNS = (
    "timestamp",
    "project",
    "modality",
    "provider",
    "model_id",
    "input_units",
    "output_units",
    "cost_usd",
    "pricing_source",
    "status",
)


def _parse_iso_date_arg(value: str, *, end_of_day: bool) -> float:
    """Parse YYYY-MM-DD into a UTC timestamp for CLI use.

    With `end_of_day=True`, advance one day so the timestamp is the
    exclusive upper bound for "include all of YYYY-MM-DD."
    """
    import datetime as _dt

    try:
        d = _dt.datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=_dt.UTC)
    except ValueError as e:
        console.print(
            f"[red]Invalid date {value!r}: expected YYYY-MM-DD[/red]"
        )
        raise typer.Exit(2) from e
    if end_of_day:
        d += _dt.timedelta(days=1)
    return d.timestamp()


@app.command(name="export-costs")
def export_costs_cmd(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
    start: str = typer.Option(
        ..., "--start", help="Start date (YYYY-MM-DD, inclusive, UTC)."
    ),
    end: str = typer.Option(
        ..., "--end", help="End date (YYYY-MM-DD, inclusive, UTC)."
    ),
    project: str = typer.Option(
        None, "--project", "-p", help="Optional project filter."
    ),
    fmt: str = typer.Option(
        "csv", "--format", "-f", help="Output format: csv (default) or json."
    ),
    output: str = typer.Option(
        "-", "--output", "-o", help="Output path; '-' (default) writes to stdout."
    ),
):
    """Export per-request cost line items for a date window.

    Output columns: timestamp, project, modality, provider, model_id,
    input_units, output_units, cost_usd, pricing_source, status.

    Pair with `voicegw reconcile` (Phase 4.3) to compare against a
    provider's invoice.
    """
    if fmt not in ("csv", "json"):
        console.print(f"[red]Unknown format: {fmt}. Use 'csv' or 'json'.[/red]")
        raise typer.Exit(2)

    gw = _load_gateway(config)
    if gw.storage is None:
        console.print(
            "[yellow]Cost tracking is not enabled in voicegw.yaml[/yellow]"
        )
        raise typer.Exit(1)

    start_ts = _parse_iso_date_arg(start, end_of_day=False)
    end_ts = _parse_iso_date_arg(end, end_of_day=True)

    rows = asyncio.run(
        gw.storage.get_requests_in_window(
            start_ts=start_ts, end_ts=end_ts, project=project
        )
    )

    import io
    import json as _json
    import sys

    buf = io.StringIO()
    if fmt == "csv":
        import csv

        writer = csv.writer(buf)
        writer.writerow(_EXPORT_COLUMNS)
        for r in rows:
            writer.writerow([r.get(col, "") for col in _EXPORT_COLUMNS])
    else:
        export_rows = [
            {col: r.get(col) for col in _EXPORT_COLUMNS} for r in rows
        ]
        _json.dump(export_rows, buf, default=str, indent=2)
        buf.write("\n")

    payload = buf.getvalue()
    if output == "-":
        sys.stdout.write(payload)
    else:
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            out_path.write_text(payload, encoding="utf-8")
        except OSError as e:
            console.print(f"[red]Failed to write {output}: {e}[/red]")
            raise typer.Exit(1) from e
        console.print(
            f"[green]Wrote {len(rows)} record(s) to {output}[/green]"
        )


@app.command(name="reconcile")
def reconcile_cmd(
    provider: str = typer.Option(
        ..., "--provider", help="Provider: openai, deepgram, or cartesia."
    ),
    start: str = typer.Option(
        ..., "--start", help="Start date (YYYY-MM-DD, inclusive, UTC)."
    ),
    end: str = typer.Option(
        ..., "--end", help="End date (YYYY-MM-DD, inclusive, UTC)."
    ),
    provider_usage_file: str = typer.Option(
        ...,
        "--provider-usage-file",
        help="Path to the provider's normalized usage file (CSV or JSON).",
    ),
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
    fmt: str = typer.Option(
        "text", "--format", "-f", help="Output format: text (default), csv, or json."
    ),
):
    """Diff VG's logged costs against a provider's usage export.

    See `docs/reference/reconcile-formats.md` for the per-provider
    file schema. Pair with `voicegw export-costs` to surface VG's
    side of the comparison if you want to inspect the raw rows.
    """
    from voicegateway import reconcile as _reconcile

    if provider not in _reconcile.SUPPORTED_PROVIDERS:
        console.print(
            f"[red]Unsupported provider: {provider!r}. "
            f"Supported: {', '.join(_reconcile.SUPPORTED_PROVIDERS)}[/red]"
        )
        raise typer.Exit(2)
    if fmt not in ("text", "csv", "json"):
        console.print(f"[red]Unknown format: {fmt}. Use text, csv, or json.[/red]")
        raise typer.Exit(2)

    gw = _load_gateway(config)
    if gw.storage is None:
        console.print(
            "[yellow]Cost tracking is not enabled in voicegw.yaml[/yellow]"
        )
        raise typer.Exit(1)

    start_ts = _parse_iso_date_arg(start, end_of_day=False)
    end_ts = _parse_iso_date_arg(end, end_of_day=True)

    records = asyncio.run(
        gw.storage.get_requests_in_window(start_ts=start_ts, end_ts=end_ts)
    )

    try:
        lines = _reconcile.reconcile(provider, records, Path(provider_usage_file))
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(2) from e
    except (ValueError, KeyError) as e:
        console.print(f"[red]Failed to parse provider usage file: {e}[/red]")
        raise typer.Exit(2) from e

    import sys

    if fmt == "csv":
        sys.stdout.write(_reconcile.format_csv(lines))
    elif fmt == "json":
        sys.stdout.write(_reconcile.format_json(lines))
    else:
        sys.stdout.write(_reconcile.format_text(lines, provider))


@app.command(name="mcp")
def mcp_cmd(
    transport: str = typer.Option(
        "stdio",
        "--transport",
        "-t",
        help="Transport layer: 'stdio' for local agents, 'http' for remote/SSE.",
    ),
    host: str = typer.Option("127.0.0.1", "--host", help="HTTP bind host (http only)"),
    port: int = typer.Option(8090, "--port", "-p", help="HTTP bind port (http only)"),
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
):
    """Start the VoiceGateway MCP server.

    Use stdio transport for local agents (Claude Code, Cursor):

        voicegw mcp --transport stdio

    Use HTTP/SSE for remote access or team gateways:

        voicegw mcp --transport http --port 8090

    Authentication (HTTP only) via VOICEGW_MCP_TOKEN env var.
    """
    if transport not in ("stdio", "http"):
        console.print(
            f"[red]Unknown transport: {transport}. Use 'stdio' or 'http'.[/red]"
        )
        raise typer.Exit(1)

    try:
        from voicegateway.mcp.server import serve_http, serve_stdio
    except ImportError as e:
        console.print(
            "[red]MCP dependencies not installed. "
            "Run: pip install 'voicegateway[mcp]'[/red]"
        )
        raise typer.Exit(1) from e

    gw = _load_gateway(config)

    if transport == "stdio":
        asyncio.run(serve_stdio(gw))
    else:
        console.print(
            f"[green]VoiceGateway MCP server listening on http://{host}:{port}/sse[/green]"
        )
        asyncio.run(serve_http(gw, host=host, port=port))


if __name__ == "__main__":
    app()
