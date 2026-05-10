"""``voicegw start`` / ``stop`` / ``restart`` lifecycle commands.

Implements the lifecycle slice of REQ-VG-ONBOARD-004. Each command
delegates to ``DaemonManager`` (the platform-agnostic facade in
``voicegateway/cli/daemon/``) and surfaces a plain-language result.

This module deliberately covers only three commands this iteration.
The full TODO bullet listed six (``start``, ``stop``, ``restart``,
``status``, ``logs``, ``uninstall-daemon``); the other three land
in separate v0.1.0 commits because:

  - ``status`` collides with v0.0.5's existing provider-status
    command. design.md decision 4 calls for the daemon view to
    appear FIRST then the provider view; that update happens in
    a dedicated iteration that touches ``voicegateway/cli/status.py``.
  - ``uninstall-daemon`` has its own TODO bullet for the rich
    preserved-state output (per design decision 5: state explicitly
    what was preserved and how to remove it manually).
  - ``logs`` collides with v0.0.5's request-logs command. The
    decision (keep ``voicegw logs`` as request logs; daemon logs
    surface through ``voicegw doctor`` and the OS-native log
    pathway) is in Discovered work.
"""

from __future__ import annotations

import typer

from voicegateway.cli._app import app, console


@app.command()
def start() -> None:
    """Bring the background daemon up.

    Calls ``DaemonManager.start()`` which delegates to the
    platform backend (LaunchAgent on macOS, systemd --user on
    Linux, Scheduled Task on Windows). Idempotent: starting an
    already-running daemon is a no-op on every backend.
    """
    from voicegateway.cli.daemon import DaemonManager

    try:
        DaemonManager().start()
    except RuntimeError as exc:
        console.print(f"[red]Failed to start daemon:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print("[green]Daemon started.[/green]")


@app.command()
def stop() -> None:
    """Bring the background daemon down.

    Calls ``DaemonManager.stop()``. Idempotent: stopping an
    already-stopped daemon is a no-op. Per design.md decision 5,
    this does NOT remove the OS-level registration; the daemon
    will still auto-start at next login. Use ``voicegw
    uninstall-daemon`` for that.
    """
    from voicegateway.cli.daemon import DaemonManager

    try:
        DaemonManager().stop()
    except RuntimeError as exc:
        console.print(f"[red]Failed to stop daemon:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print("[green]Daemon stopped.[/green]")


@app.command()
def restart() -> None:
    """Restart the background daemon (stop + start).

    Calls ``DaemonManager.restart()``. The platform backend uses
    the most efficient OS primitive (``kickstart -k`` on macOS,
    ``systemctl restart`` on Linux); the cli surface is the same
    everywhere.
    """
    from voicegateway.cli.daemon import DaemonManager

    try:
        DaemonManager().restart()
    except RuntimeError as exc:
        console.print(f"[red]Failed to restart daemon:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print("[green]Daemon restarted.[/green]")
