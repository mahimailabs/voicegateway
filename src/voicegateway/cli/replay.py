"""``voicegw replay`` command: signpost the dashboard's Replay page.

v0.3.0 minimal: prints a one-line "open replay in dashboard at <url>"
hint for a given session id. The Foundry explicitly scopes this
command as signposting only (it does not render the timeline in the
terminal). Future scope (v0.3.x or v0.4.0) could embed a Textual-based
mini-replay using v0.1.1's TUI primitives.

Path correction note: the Foundry's text listed
``voicegw/cli/replay.py``; the actual CLI subpackage is
``voicegateway/cli/`` (per the v0.1.2 project-polish layout). The
file lives here, in the correct package, so the existing Typer
``app`` registry from ``voicegateway.cli._app`` picks it up via the
``cli/__init__.py`` import side-effect.

The dashboard host/port default to ``127.0.0.1:9090`` (the
``voicegw dashboard`` defaults from v0.0.5). The ``--dashboard-url``
override lets users running behind a reverse proxy or on a different
host print the correct URL.
"""

from __future__ import annotations

import typer

from voicegateway.cli._app import app, console


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
    """Print the URL of the dashboard Replay page for a session.

    The dashboard must already be running (``voicegw dashboard``).
    This command does not render the replay in the terminal; it is
    a signpost to the dashboard's graphical timeline.
    """
    if not session_id.strip():
        console.print("[red]session_id is required.[/red]")
        raise typer.Exit(code=1)
    base = dashboard_url.rstrip("/")
    target = f"{base}/sessions/{session_id}/replay"
    console.print(f"[bold]Replay:[/bold] {target}")
    console.print("[dim]The dashboard must be running: voicegw dashboard.[/dim]")


__all__ = ["replay_cmd"]
