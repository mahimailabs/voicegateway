"""CLI for VoiceGateway."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console

app = typer.Typer(
    name="voicegw",
    help="VoiceGateway: cost tracking and reconciliation for LiveKit voice agents",
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


def _load_gateway(config_path: str | None):
    from voicegateway.core.gateway import Gateway

    try:
        return Gateway(config_path=config_path)
    except Exception as e:
        console.print(f"[red]Error loading config: {e}[/red]")
        raise typer.Exit(1) from e


def _parse_iso_date_arg(value: str, *, end_of_day: bool) -> float:
    """Parse YYYY-MM-DD into a UTC timestamp for CLI use.

    With `end_of_day=True`, advance one day so the timestamp is the
    exclusive upper bound for "include all of YYYY-MM-DD."

    Shared by ``export-costs`` and ``reconcile``. Lives in this
    module until both have been carved out, then moves to a shared
    helpers module in the section-2 finalisation commit.
    """
    import datetime as _dt

    try:
        d = _dt.datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=_dt.UTC)
    except ValueError as e:
        console.print(f"[red]Invalid date {value!r}: expected YYYY-MM-DD[/red]")
        raise typer.Exit(2) from e
    if end_of_day:
        d += _dt.timedelta(days=1)
    return d.timestamp()


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
    threshold: float = typer.Option(
        5.0,
        "--threshold",
        help=(
            "Flag rows whose |cost diff %| exceeds this threshold "
            "(default 5.0). The v0.0.4 disclosure expects LLM "
            "estimates to drift up to ~5%."
        ),
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
        console.print("[yellow]Cost tracking is not enabled in voicegw.yaml[/yellow]")
        raise typer.Exit(1)

    start_ts = _parse_iso_date_arg(start, end_of_day=False)
    end_ts = _parse_iso_date_arg(end, end_of_day=True)

    records = asyncio.run(
        gw.storage.get_requests_in_window(start_ts=start_ts, end_ts=end_ts)
    )

    try:
        lines = _reconcile.reconcile(
            provider,
            records,
            Path(provider_usage_file),
            threshold_pct=threshold,
        )
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
        sys.stdout.write(
            _reconcile.format_json(
                lines,
                provider=provider,
                period_start=start,
                period_end=end,
            )
        )
    else:
        # Colorize flagged rows only on a real TTY; piped output and
        # CliRunner captures stay plain text.
        sys.stdout.write(
            _reconcile.format_text(lines, provider, colorize=sys.stdout.isatty())
        )


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
