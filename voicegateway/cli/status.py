"""``voicegw status`` command.

Carved out of voicegateway/cli/_legacy.py during the v0.1.0 section-2
refactor and extended in section 5 to show DAEMON status first, then
provider status (per design.md decision 4: "voicegw status order:
Daemon status first, then provider status").

The two sections are independent: a missing daemon backend (e.g.
construction error inside a hardened sandbox) does NOT block the
provider table from rendering. ``voicegw doctor`` is the deeper
diagnostic when the daemon side reports something concerning here.
"""

from __future__ import annotations

import typer
from rich.table import Table

from voicegateway.cli._app import app, console
from voicegateway.cli._helpers import _load_gateway


@app.command()
def status(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
    project: str = typer.Option(None, "--project", "-p", help="Filter by project ID"),
) -> None:
    """Show daemon status, then provider status."""
    _print_daemon_status()
    console.print()  # one blank line between the two sections
    _print_provider_status(config=config, project=project)


def _print_daemon_status() -> None:
    """Render the daemon-status section.

    Calls ``DaemonManager().status()`` and reports the three
    Protocol-required fields (registered / running / pid). On any
    error (e.g., backend module fails to load), prints a yellow
    soft-warn line and returns; the provider section still renders.
    """
    try:
        from voicegateway.cli.daemon import DaemonManager

        info = DaemonManager().status()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]Daemon status unavailable: {exc}[/yellow]")
        return

    registered = bool(info.get("registered"))
    running = bool(info.get("running"))
    pid = info.get("pid")

    table = Table(title="Daemon", show_header=False, box=None)
    table.add_column(style="dim")
    table.add_column()
    table.add_row(
        "Registered",
        "[green]yes[/green]"
        if registered
        else "[yellow]no (run `voicegw onboard --install-daemon`)[/yellow]",
    )
    table.add_row(
        "Running",
        "[green]yes[/green]" if running else "[yellow]no[/yellow]",
    )
    table.add_row("PID", str(pid) if pid else "[dim]-[/dim]")
    console.print(table)


def _print_provider_status(*, config: str | None, project: str | None) -> None:
    """Render the provider-status table (v0.0.5 behaviour)."""
    gw = _load_gateway(config)
    cfg = gw.config

    if project and cfg.projects and project not in cfg.projects:
        console.print(f"[red]Unknown project: {project}[/red]")
        raise typer.Exit(1)

    table = Table(title=f"Provider Status{f' — {project}' if project else ''}")
    table.add_column("Provider", style="cyan")
    table.add_column("Configured", style="green")
    table.add_column("Models")

    for provider_name, provider_config in sorted(cfg.providers.items()):
        has_key = bool(provider_config.get("api_key")) or provider_name in (
            "ollama",
            "whisper",
            "kokoro",
            "piper",
        )
        model_count = 0
        for modality_models in cfg.models.values():
            if isinstance(modality_models, dict):
                for model_cfg in modality_models.values():
                    if (
                        isinstance(model_cfg, dict)
                        and model_cfg.get("provider") == provider_name
                    ):
                        model_count += 1
        status_str = "[green]Yes[/green]" if has_key else "[red]No API key[/red]"
        table.add_row(provider_name, status_str, str(model_count))

    console.print(table)
