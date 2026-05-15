"""``voicegw replay`` command: signpost the dashboard's Replay page."""

from __future__ import annotations

import typer

from voicegateway.cli._app import app, console
from voicegateway.cli.base_cli import BaseCli

_cli = BaseCli()


@app.command(name="replay")
def replay_cmd(
    session_id: str = typer.Argument(
        ...,
        help="Session id to open in the dashboard's Replay page.",
    ),
    dashboard_url: str = typer.Option(
        "http://127.0.0.1:9090",
        "--dashboard-url",
        help="Base URL of the dashboard (default: 127.0.0.1:9090).",
    ),
) -> None:
    """Print the URL of the dashboard Replay page for a session."""
    if not session_id.strip():
        _cli.fail("session_id is required.")
    base = dashboard_url.rstrip("/")
    target = f"{base}/sessions/{session_id}/replay"
    console.print(f"[bold]Replay:[/bold] {target}")
    _cli.dim("The dashboard must be running: voicegw dashboard.")


__all__ = ["replay_cmd"]
