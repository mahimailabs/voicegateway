"""``voicegw reconcile`` command."""

from __future__ import annotations

from pathlib import Path

import typer

from voicegateway.cli._app import app
from voicegateway.cli.base_cli import BaseCli
from voicegateway.services import reconciliation_service as _reconcile
from voicegateway.utils.cli._shared import _parse_iso_date_arg

_cli = BaseCli()


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
    if provider not in _reconcile.SUPPORTED_PROVIDERS:
        _cli.fail(
            f"Unsupported provider: {provider!r}. "
            f"Supported: {', '.join(_reconcile.SUPPORTED_PROVIDERS)}",
            code=2,
        )
    if fmt not in ("text", "csv", "json"):
        _cli.fail(f"Unknown format: {fmt}. Use text, csv, or json.", code=2)

    gw = _cli.require_gateway(config)
    storage = _cli.require_storage(gw)

    start_ts = _parse_iso_date_arg(start, end_of_day=False)
    end_ts = _parse_iso_date_arg(end, end_of_day=True)

    records = _cli.async_run(
        storage.get_requests_in_window(start_ts=start_ts, end_ts=end_ts)
    )

    try:
        lines = _reconcile.reconcile(
            provider,
            records,
            Path(provider_usage_file),
            threshold_pct=threshold,
        )
    except FileNotFoundError as exc:
        _cli.fail(str(exc), code=2)
    except (ValueError, KeyError) as exc:
        _cli.fail(f"Failed to parse provider usage file: {exc}", code=2)

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
