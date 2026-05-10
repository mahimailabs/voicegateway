"""``voicegw status`` command.

Carved out of voicegateway/cli/_legacy.py during the v0.1.0 section-2
refactor. Shows provider status (configured/missing keys, model
counts) for the active gateway config.

Note: in v0.1.0 (per design.md decision 4) ``voicegw status`` will
eventually show daemon status FIRST then provider status. That
re-ordering lands as a separate task in section 5 once the
DaemonManager facade exists; this carve-out preserves v0.0.5
semantics verbatim so the move is purely structural.
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
    """Show provider status."""
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
