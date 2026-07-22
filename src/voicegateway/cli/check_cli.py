"""``voicegw check`` command (with ``smoke-test`` as a hidden deprecated alias)."""

from __future__ import annotations

import asyncio

import typer

from voicegateway.cli._app import app
from voicegateway.cli.base_cli import BaseCli
from voicegateway.core.gateway_factory import reset_gateway
from voicegateway.utils.cli.check import (
    _print_check_report,
    check_active_project,
    run_check,
)

_cli = BaseCli()


def _run(config: str | None, project: str | None) -> None:
    """Shared implementation for ``check`` and its ``smoke-test`` alias."""
    gw = _cli.require_gateway(config)

    rows: list[tuple[str, bool, str]] = []  # (label, passed, message)

    def add(label: str, passed: bool, message: str = "") -> None:
        rows.append((label, passed, message))

    if gw.storage is None:
        add(
            "config",
            False,
            "Cost tracking disabled in voicegw.yaml; check needs storage.",
        )
        _print_check_report(rows)
        raise typer.Exit(1)
    add("config", True, str(gw.config.cost_tracking.get("db_path", "(default)")))

    if project is not None and project not in gw.config.projects:
        known = ", ".join(sorted(gw.config.projects)) or "(none)"
        add("active project", False, f"Unknown project '{project}'. Known: {known}.")
        _print_check_report(rows)
        raise typer.Exit(1)

    active = project or check_active_project(gw)
    if active is None:
        add("active project", False, "No project configured.")
        _print_check_report(rows)
        raise typer.Exit(1)
    add("active project", True, active)

    try:
        # Session id, write, and read-back all run inside this single event loop.
        asyncio.run(run_check(gw, active, add))
        _print_check_report(rows)
        if any(not passed for _, passed, _ in rows):
            raise typer.Exit(1)
    finally:
        reset_gateway()


@app.command(name="check")
def check(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
    project: str = typer.Option(
        None, "--project", "-p", help="Project to check (default: active project)"
    ),
) -> None:
    """Verify metering + storage end to end.

    Drives one synthetic instrumented request and confirms a request row and a
    session row land in storage. Framework-agnostic: no providers, no network.
    """
    _run(config, project)


@app.command(name="smoke-test", hidden=True)
def smoke_test(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
    project: str = typer.Option(
        None, "--project", "-p", help="Project to check (default: active project)"
    ),
) -> None:
    """Deprecated alias for ``voicegw check``."""
    _run(config, project)
