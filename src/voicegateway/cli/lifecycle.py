"""``voicegw start`` / ``stop`` / ``restart`` / ``daemon-logs`` / ``uninstall-daemon`` commands."""

from __future__ import annotations

import typer

from voicegateway.cli._app import app, console
from voicegateway.utils.cli._shared import _config_home


@app.command()
def start() -> None:
    """Bring the background daemon up."""
    from voicegateway.cli.daemon import DaemonManager

    try:
        DaemonManager().start()
    except RuntimeError as exc:
        console.print(f"[red]Failed to start daemon:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print("[green]Daemon started.[/green]")


@app.command()
def stop() -> None:
    """Bring the background daemon down."""
    from voicegateway.cli.daemon import DaemonManager

    try:
        DaemonManager().stop()
    except RuntimeError as exc:
        console.print(f"[red]Failed to stop daemon:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print("[green]Daemon stopped.[/green]")


@app.command()
def restart() -> None:
    """Restart the background daemon (stop + start)."""
    from voicegateway.cli.daemon import DaemonManager

    try:
        DaemonManager().restart()
    except RuntimeError as exc:
        console.print(f"[red]Failed to restart daemon:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print("[green]Daemon restarted.[/green]")


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
    from voicegateway.cli.daemon import DaemonManager

    try:
        output = DaemonManager().logs(tail=tail)
    except RuntimeError as exc:
        console.print(f"[red]Failed to read daemon logs:[/red] {exc}")
        raise typer.Exit(1) from exc

    if not output.strip():
        console.print(
            "[dim]No daemon logs yet. Try `voicegw start` first, then re-run.[/dim]"
        )
        return

    console.print(output, soft_wrap=True)


@app.command(name="uninstall-daemon")
def uninstall_daemon() -> None:
    """Remove the daemon registration only."""
    from voicegateway.cli.daemon import DaemonManager

    try:
        DaemonManager().uninstall()
    except RuntimeError as exc:
        console.print(f"[red]Failed to uninstall daemon:[/red] {exc}")
        raise typer.Exit(1) from exc

    home = _config_home()
    console.print("[green]Daemon registration removed.[/green]\n")
    console.print("[bold]Preserved (NOT removed):[/bold]")
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
