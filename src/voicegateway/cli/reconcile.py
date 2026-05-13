"""``voicegw reconcile`` command."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from voicegateway.cli._app import app, console
from voicegateway.utils.cli._shared import _load_gateway, _parse_iso_date_arg


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
            "(default 5.0). The disclosure expects LLM "
            "estimates to drift up to ~5%."
        ),
    ),
) -> None:
    """Diff VG's logged costs against a provider's usage export."""
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
        sys.stdout.write(
            _reconcile.format_text(lines, provider, colorize=sys.stdout.isatty())
        )
