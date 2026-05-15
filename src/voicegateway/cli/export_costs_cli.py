"""``voicegw export-costs`` command."""

from __future__ import annotations

from pathlib import Path

import typer

from voicegateway.cli._app import app
from voicegateway.cli.base_cli import BaseCli
from voicegateway.utils.cli._shared import _parse_iso_date_arg
from voicegateway.utils.cli.export_costs import _EXPORT_COLUMNS, _format_export_row

_cli = BaseCli()


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
        _cli.fail(f"Unknown format: {fmt}. Use 'csv' or 'json'.", code=2)

    gw = _cli.require_gateway(config)
    storage = _cli.require_storage(gw)

    start_ts = _parse_iso_date_arg(start, end_of_day=False)
    end_ts = _parse_iso_date_arg(end, end_of_day=True)

    rows = _cli.async_run(
        storage.get_requests_in_window(
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
        except OSError as exc:
            _cli.fail(f"Failed to write {output}: {exc}")
        _cli.success(f"Wrote {len(rows)} record(s) to {output}")
