"""``voicegw start`` / ``stop`` / ``restart`` / ``daemon-logs`` / ``uninstall-daemon`` commands."""

from __future__ import annotations

import typer

from voicegateway.cli import daemon as _daemon
from voicegateway.cli._app import app
from voicegateway.cli.base_cli import BaseCli
from voicegateway.utils.cli._shared import _config_home

_cli = BaseCli()


@app.command()
def start() -> None:
    """Bring the background daemon up."""
    try:
        _daemon.DaemonManager().start()
    except RuntimeError as exc:
        _cli.fail(f"Failed to start daemon: {exc}")
    _cli.success("Daemon started.")


@app.command()
def stop() -> None:
    """Bring the background daemon down."""
    try:
        _daemon.DaemonManager().stop()
    except RuntimeError as exc:
        _cli.fail(f"Failed to stop daemon: {exc}")
    _cli.success("Daemon stopped.")


@app.command()
def restart() -> None:
    """Restart the background daemon (stop + start)."""
    try:
        _daemon.DaemonManager().restart()
    except RuntimeError as exc:
        _cli.fail(f"Failed to restart daemon: {exc}")
    _cli.success("Daemon restarted.")


@app.command(name="daemon-logs")
def daemon_logs(
    tail: int = typer.Option(
        100,
        "--tail",
        "-n",
        help="Number of recent log lines to print.",
    ),
) -> None:
    """Tail the OS-native daemon log stream."""
    try:
        output = _daemon.DaemonManager().logs(tail=tail)
    except RuntimeError as exc:
        _cli.fail(f"Failed to read daemon logs: {exc}")

    if not output.strip():
        _cli.dim("No daemon logs yet. Try `voicegw start` first, then re-run.")
        return

    _cli.console.print(output, soft_wrap=True)


@app.command(name="uninstall-daemon")
def uninstall_daemon() -> None:
    """Remove the daemon registration only."""
    try:
        _daemon.DaemonManager().uninstall()
    except RuntimeError as exc:
        _cli.fail(f"Failed to uninstall daemon: {exc}")

    home = _config_home()
    _cli.success("Daemon registration removed.\n")
    _cli.console.print("[bold]Preserved (NOT removed):[/bold]")
    _cli.console.print(f"  Config file:     {home / 'voicegw.yaml'}", soft_wrap=True)
    _cli.console.print(f"  Call database:   {home / 'voicegw.db'}", soft_wrap=True)
    _cli.console.print(
        "  Managed provider keys (Fernet-encrypted) inside the "
        "managed_providers table of the call database."
    )
    _cli.console.print()
    _cli.console.print("[bold]To remove the preserved state manually:[/bold]")
    _cli.console.print(f"  [cyan]rm -rf {home}[/cyan]", soft_wrap=True)
    _cli.dim(
        "A `voicegw purge-data` command is planned for a later "
        "release; for now the manual command above is the supported "
        "path."
    )
