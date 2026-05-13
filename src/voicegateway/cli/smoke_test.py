"""``voicegw smoke-test`` command."""

from __future__ import annotations

import asyncio

import typer

from voicegateway.cli._app import app
from voicegateway.utils.cli._shared import _load_gateway
from voicegateway.utils.cli.smoke_test import (
    _print_smoke_report,
    _run_smoke_health_checks,
    _run_smoke_pipeline_checks,
    _smoke_active_project,
)


@app.command(name="smoke-test")
def smoke_test(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
    project: str = typer.Option(
        None, "--project", "-p", help="Project to test (default: active project)"
    ),
    live: bool = typer.Option(
        False,
        "--live",
        help=(
            "Run health-check probes against each provider's API in addition "
            "to the structural pipeline checks. Requires real credentials and "
            "network access; takes a few seconds per provider."
        ),
    ),
) -> None:
    """Verify the v0.0.5 inference pipeline end-to-end without LiveKit."""
    from voicegateway.inference._factory import reset_gateway

    gw = _load_gateway(config)

    rows: list[tuple[str, bool, str]] = []  # (label, passed, message)

    def add(label: str, passed: bool, message: str = "") -> None:
        rows.append((label, passed, message))

    # 1. Config + project resolution.
    if gw.storage is None:
        add(
            "config",
            False,
            "Cost tracking disabled in voicegw.yaml; smoke-test needs storage.",
        )
        _print_smoke_report(rows)
        raise typer.Exit(1)
    add("config", True, str(gw.config.cost_tracking.get("db_path", "(default)")))

    if project is not None and project not in gw.config.projects:
        known = ", ".join(sorted(gw.config.projects)) or "(none)"
        add(
            "active project",
            False,
            f"Unknown project '{project}'. Known projects: {known}.",
        )
        _print_smoke_report(rows)
        raise typer.Exit(1)

    active = project or _smoke_active_project(gw)
    if active is None:
        add(
            "active project",
            False,
            "No project configured; cannot select a provider key.",
        )
        _print_smoke_report(rows)
        raise typer.Exit(1)
    add("active project", True, active)

    try:
        # 2. Per-modality construction with stubbed LiveKit plugins.
        asyncio.run(_run_smoke_pipeline_checks(gw, active, add))

        # 3. Optional live health checks.
        if live:
            asyncio.run(_run_smoke_health_checks(gw, active, add))

        _print_smoke_report(rows)
        if any(not passed for _, passed, _ in rows):
            raise typer.Exit(1)
    finally:
        reset_gateway()
