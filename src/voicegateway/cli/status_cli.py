"""``voicegw status`` command."""

from __future__ import annotations

import typer

from voicegateway.cli._app import app, console
from voicegateway.utils.cli.status import _print_daemon_status, _print_provider_status


@app.command()
def status(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
    project: str = typer.Option(None, "--project", "-p", help="Filter by project ID"),
) -> None:
    """Show daemon status, then provider status."""
    _print_daemon_status()
    console.print()  # one blank line between the two sections
    _print_provider_status(config=config, project=project)
