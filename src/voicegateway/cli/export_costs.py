"""``voicegw export-costs`` command."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import typer

from voicegateway.cli._app import app, console
from voicegateway.cli._helpers import _load_gateway, _parse_iso_date_arg

_EXPORT_COLUMNS = (
    "timestamp",
    "project",
    "modality",
    "provider",
    "model",
    "input_units",
    "output_units",
    "calculated_cost_usd",
    "pricing_source",
    "status",
)


_EXPORT_KEY_MAP = {
    "model": "model_id",
    "calculated_cost_usd": "cost_usd",
}


def _format_export_value(column: str, value: Any) -> Any:
    """Format one cell of an export row."""
    import datetime as _dt
    from decimal import Decimal

    if value is None:
        return ""
    if column == "timestamp":
        try:
            return _dt.datetime.fromtimestamp(float(value), tz=_dt.UTC).isoformat()
        except (TypeError, ValueError, OSError):
            return value
    if column == "calculated_cost_usd":
        try:
            return format(Decimal(str(float(value))), "f")
        except (TypeError, ValueError):
            return value
    return value


def _format_export_row(record: dict[str, Any]) -> dict[str, Any]:
    """Project a storage row into the design-spec export schema."""
    out: dict[str, Any] = {}
    for col in _EXPORT_COLUMNS:
        src = _EXPORT_KEY_MAP.get(col, col)
        out[col] = _format_export_value(col, record.get(src))
    return out


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
) -> None:
    """Export per-request cost line items for a date window."""
    if fmt not in ("csv", "json"):
        console.print(f"[red]Unknown format: {fmt}. Use 'csv' or 'json'.[/red]")
        raise typer.Exit(2)

    gw = _load_gateway(config)
    if gw.storage is None:
        console.print("[yellow]Cost tracking is not enabled in voicegw.yaml[/yellow]")
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
            formatted = _format_export_row(r)
            writer.writerow([formatted[col] for col in _EXPORT_COLUMNS])
    else:
        for r in rows:
            _json.dump(_format_export_row(r), buf, default=str)
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
        console.print(f"[green]Wrote {len(rows)} record(s) to {output}[/green]")
