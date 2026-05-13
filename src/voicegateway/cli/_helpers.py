"""Cross-command helpers shared between cli submodules."""

from __future__ import annotations

import typer

from voicegateway.cli._app import console


def _load_gateway(config_path: str | None):
    from voicegateway.core.gateway import Gateway

    try:
        return Gateway(config_path=config_path)
    except Exception as e:
        console.print(f"[red]Error loading config: {e}[/red]")
        raise typer.Exit(1) from e


def _parse_iso_date_arg(value: str, *, end_of_day: bool) -> float:
    """Parse YYYY-MM-DD into a UTC timestamp for CLI use."""
    import datetime as _dt

    try:
        d = _dt.datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=_dt.UTC)
    except ValueError as e:
        console.print(f"[red]Invalid date {value!r}: expected YYYY-MM-DD[/red]")
        raise typer.Exit(2) from e
    if end_of_day:
        d += _dt.timedelta(days=1)
    return d.timestamp()
