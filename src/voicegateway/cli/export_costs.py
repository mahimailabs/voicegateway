"""``voicegw export-costs`` command.

Carved out of voicegateway/cli/_legacy.py during the v0.1.0 section-2
refactor. Streams per-request cost line items as CSV or JSONL for a
date window. Pair with ``voicegw reconcile`` to verify against a
provider's invoice export.

Owns the export schema (``_EXPORT_COLUMNS``, ``_EXPORT_KEY_MAP``)
and its two formatter helpers. The shared ``_parse_iso_date_arg``
date parser stays in ``_legacy.py`` for now because ``reconcile`` is
also a consumer; the helper moves to a shared module once both
commands are carved out.
"""

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

# Map output column names to the storage row keys they project from.
# Storage uses `model_id` and `cost_usd`; the export schema (design
# §2.1) names them `model` and `calculated_cost_usd`. The other 8
# column names match storage keys directly.
_EXPORT_KEY_MAP = {
    "model": "model_id",
    "calculated_cost_usd": "cost_usd",
}


def _format_export_value(column: str, value: Any) -> Any:
    """Format one cell of an export row.

    - timestamp: storage Unix-epoch float -> ISO-8601 UTC string.
    - calculated_cost_usd: float -> fixed-point Decimal string so
      sub-cent costs do not render in scientific notation
      (e.g. 1e-05 -> "0.00001"). The float -> str(Decimal(str(...)))
      hop dodges binary-precision artifacts.
    - everything else: pass through (csv.writer / json.dump handle
      the rest).
    """
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
    """Export per-request cost line items for a date window.

    Output columns: timestamp (ISO-8601 UTC), project, modality,
    provider, model, input_units, output_units,
    calculated_cost_usd (fixed-point, no scientific notation),
    pricing_source, status.

    Output formats:
    - csv (default): header row + one data row per request.
    - json: JSONL (one JSON object per line; no outer array, no
      indent). Streamable; consumers iterate `json.loads` per line.

    Pair with `voicegw reconcile` (Phase 4.3) to compare against a
    provider's invoice.
    """
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
        # JSONL: one JSON object per line, no outer array, no indent.
        # Per design §2.1 and TODO 4.1 #4. Streamable; downstream
        # consumers can `for line in f: row = json.loads(line)`.
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
