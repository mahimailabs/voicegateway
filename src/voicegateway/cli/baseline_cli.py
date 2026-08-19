"""``voicegw baseline pin`` and ``voicegw baseline check``: a drift gate for CI.

Nothing here imports an optional SDK, so the bare-install rule from #241 holds:
a `pip install voicegateway` with no extras can run both commands.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from voicegateway.cli._app import app, console
from voicegateway.cli.base_cli import BaseCli
from voicegateway.services.baseline_service import (
    BASELINE_VERSION,
    DEFAULT_MIN_SAMPLES,
    DEFAULT_TOLERANCES,
    EXIT_DRIFT,
    EXIT_INSUFFICIENT,
    EXIT_OK,
    compare,
)

_cli = BaseCli()

baseline_app = typer.Typer(
    help="Pin a known-good window of figures, then fail CI when a later one drifts."
)


async def _p95_ttfb(
    storage: Any, modality: str, period: str, project: str | None
) -> dict[str, Any]:
    """p95 first-byte for one stage, with the count it was taken over.

    Computed here rather than in SQL because SQLite has no percentile function
    and the turns aggregate already sets the precedent of pulling the column and
    cutting it in Python.
    """
    import statistics

    from sqlalchemy import text as _text

    from voicegateway.repository.cost_repository import resolve_window

    since, until = resolve_window(period)
    where = "WHERE timestamp >= :since AND modality = :modality AND ttfb_ms IS NOT NULL"
    params: dict[str, Any] = {"since": since, "modality": modality}
    if until is not None:
        where += " AND timestamp < :until"
        params["until"] = until
    if project:
        where += " AND project = :project"
        params["project"] = project
    async with storage._conn.session() as db:
        result = await db.execute(
            _text(f"SELECT ttfb_ms FROM requests {where}"), params
        )
        values = [float(r[0]) for r in result]
    if not values:
        return {"value": None, "samples": 0}
    if len(values) == 1:
        return {"value": values[0], "samples": 1}
    cuts = statistics.quantiles(values, n=100, method="inclusive")
    return {"value": cuts[94], "samples": len(values)}


async def _collect(storage: Any, period: str, project: str | None) -> dict[str, Any]:
    """The current window's figures, each with the sample count behind it.

    THE SAMPLE COUNT TRAVELS WITH THE VALUE rather than being derived later.
    A percentile over three calls and one over two hundred are different claims,
    and a file that records only the number cannot tell them apart afterwards.
    """
    from voicegateway.repository import turns_repository as turns

    metrics: dict[str, dict[str, Any]] = {}

    async with storage._conn.session() as db:
        speed = await turns.aggregate_response_speed(db)
        result = await db.execute(
            turns.text("SELECT COUNT(*) FROM turns WHERE response_speed_ms IS NOT NULL")
        )
        row = result.fetchone()
        turn_samples = int(row[0]) if row else 0
    metrics["response_speed_p50_ms"] = {
        "value": speed.get("p50_ms"),
        "samples": turn_samples,
    }
    metrics["response_speed_p95_ms"] = {
        "value": speed.get("p95_ms"),
        "samples": turn_samples,
    }

    # Stage components, straight from the rows rather than reshaped out of the
    # per-model rollup: the baseline compares one number per stage, and going
    # via by_model would make the answer depend on which models happened to be
    # in the window.
    for modality, name in (("llm", "llm_ttft_p95_ms"), ("tts", "tts_ttfb_p95_ms")):
        metrics[name] = await _p95_ttfb(storage, modality, period, project)

    summary = await storage.get_cost_summary(period, project=project)
    calls = int(summary.get("request_count") or 0)
    total = float(summary.get("total") or 0.0)
    metrics["cost_per_call_usd"] = {
        "value": (total / calls) if calls else None,
        "samples": calls,
    }

    return {
        "version": BASELINE_VERSION,
        # What identifies the window, so a baseline is reproducible rather than
        # a snapshot of an unnamed moment.
        "window": {"period": period, "project": project},
        "metrics": metrics,
    }


@baseline_app.command("pin")
def pin(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
    period: str = typer.Option("week", "--period", help="today | week | month | all"),
    project: str = typer.Option(None, "--project", "-p", help="Filter by project"),
    out: Path = typer.Option(
        Path("voicegw-baseline.json"), "--out", "-o", help="Where to write it"
    ),
) -> None:
    """Write the current window's figures to a file."""
    gw = _cli.require_gateway(config)
    storage = _cli.require_storage(gw)
    payload = _cli.async_run(_collect(storage, period, project))
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    measured = sum(1 for m in payload["metrics"].values() if m["value"] is not None)
    console.print(f"Pinned {measured} metric(s) to [cyan]{out}[/cyan] ({period}).")
    for name, m in sorted(payload["metrics"].items()):
        shown = "unmeasured" if m["value"] is None else f"{m['value']:g}"
        console.print(f"  {name}: {shown} ({m['samples']} samples)")


@baseline_app.command("check")
def check(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
    baseline: Path = typer.Option(
        Path("voicegw-baseline.json"), "--baseline", "-b", help="Pinned file to compare"
    ),
    period: str = typer.Option(None, "--period", help="Defaults to the pinned window"),
    project: str = typer.Option(None, "--project", "-p", help="Filter by project"),
    min_samples: int = typer.Option(
        DEFAULT_MIN_SAMPLES, "--min-samples", help="Below this, report insufficient"
    ),
) -> None:
    """Recompute over a later window and exit non-zero on drift.

    Exit codes are STABLE, because CI depends on them:

    * 0 every compared metric is within tolerance
    * 1 at least one metric drifted
    * 2 nothing drifted, but at least one metric could not be compared
    """
    try:
        pinned = json.loads(baseline.read_text())
    except OSError as exc:
        _cli.fail(f"cannot read {baseline}: {exc}", code=2)
    except json.JSONDecodeError as exc:
        _cli.fail(f"{baseline} is not valid JSON: {exc}", code=2)

    if pinned.get("version") != BASELINE_VERSION:
        _cli.fail(
            f"{baseline} was pinned by baseline version "
            f"{pinned.get('version')!r}; this build writes {BASELINE_VERSION}. "
            "Re-pin rather than comparing against fields that may have moved.",
            code=2,
        )

    window = pinned.get("window") or {}
    resolved_period = period or window.get("period") or "week"
    resolved_project = project if project is not None else window.get("project")

    gw = _cli.require_gateway(config)
    storage = _cli.require_storage(gw)
    current = _cli.async_run(_collect(storage, resolved_period, resolved_project))
    report = compare(pinned, current, min_samples=min_samples)

    console.print(
        f"\n[bold]Baseline check[/bold] ({resolved_period}) against {baseline}\n"
    )
    for finding in report.findings:
        colour = {
            "ok": "green",
            "drift": "red",
            "unmeasured": "yellow",
            "insufficient": "yellow",
        }[finding.status]
        console.print(f"  [{colour}]{finding.describe()}[/{colour}]")

    code = report.exit_code
    if code == EXIT_OK:
        console.print("\n[green]No drift.[/green]")
    elif code == EXIT_DRIFT:
        console.print("\n[red]Drift detected.[/red]")
    else:
        console.print("\n[yellow]Could not compare every metric.[/yellow]")
    raise typer.Exit(code)


app.add_typer(baseline_app, name="baseline")

__all__ = ["DEFAULT_TOLERANCES", "EXIT_DRIFT", "EXIT_INSUFFICIENT", "EXIT_OK", "pin"]
