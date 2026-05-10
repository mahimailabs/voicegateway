"""``voicegw start`` / ``stop`` / ``restart`` / ``uninstall-daemon`` commands.

Implements the lifecycle slice of REQ-VG-ONBOARD-004. Each command
delegates to ``DaemonManager`` (the platform-agnostic facade in
``voicegateway/cli/daemon/``) and surfaces a plain-language result.

The original TODO bullet listed six commands. ``status`` lives in
``voicegateway/cli/status.py`` (the daemon-first reorder happens
there because it has to merge with the v0.0.5 provider view); the
``logs`` command stays as v0.0.5's request-logs surface and the
daemon-log surface is reachable through the OS native viewer
(decision tracked in Discovered work).
"""

from __future__ import annotations

from pathlib import Path

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


# v0.0.5's canonical config home. v0.1.0 keeps the same path per
# design decision 2 (no migration pain), so the uninstall message
# can hard-reference it. Resolved at call time so a HOME override
# in tests gets picked up.
def _config_home() -> Path:
    return Path.home() / ".config" / "voicegateway"


@app.command(name="uninstall-daemon")
def uninstall_daemon() -> None:
    """Remove the daemon registration only.

    Per design decision 5 the preserved-state contract is: the
    config file (``voicegw.yaml``), the SQLite call DB
    (``voicegw.db``), and the encrypted managed_providers rows
    inside that DB are NOT touched. Re-running ``voicegw onboard
    --install-daemon`` re-registers without losing any state.

    Output explicitly states what was preserved and how to remove
    it manually. ``voicegw purge-data`` is deferred to a later
    release; for now the manual command is documented.
    """
    from voicegateway.cli.daemon import DaemonManager

    try:
        DaemonManager().uninstall()
    except RuntimeError as exc:
        console.print(f"[red]Failed to uninstall daemon:[/red] {exc}")
        raise typer.Exit(1) from exc

    home = _config_home()
    console.print("[green]Daemon registration removed.[/green]\n")
    console.print("[bold]Preserved (NOT removed):[/bold]")
    # ``soft_wrap=True`` keeps long tmp/CI paths on a single line.
    # Rich's default wrapping folds them mid-segment which breaks
    # both readability and any test that asserts a substring.
    console.print(f"  Config file:     {home / 'voicegw.yaml'}", soft_wrap=True)
    console.print(f"  Call database:   {home / 'voicegw.db'}", soft_wrap=True)
    console.print(
        "  Managed provider keys (Fernet-encrypted) inside the "
        "managed_providers table of the call database."
    )
    console.print()
    console.print("[bold]To remove the preserved state manually:[/bold]")
    console.print(f"  [cyan]rm -rf {home}[/cyan]", soft_wrap=True)
    console.print(
        "[dim]A `voicegw purge-data` command is planned for a later "
        "release; for now the manual command above is the supported "
        "path.[/dim]"
    )
