"""Helpers for ``voicegateway.cli.status_cli``."""

from __future__ import annotations

import typer
from rich.table import Table

from voicegateway.cli._app import console
from voicegateway.utils.cli._shared import _load_gateway


def _print_daemon_status() -> None:
    """Render the daemon-status section."""
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
    """Render the provider-status table."""
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
