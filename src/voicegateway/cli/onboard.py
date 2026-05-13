"""``voicegw onboard`` wizard."""

from __future__ import annotations

import typer

from voicegateway.cli._app import app, console
from voicegateway.core.constants import KNOWN_PROVIDERS
from voicegateway.utils.cli.onboard import (
    _install_daemon,
    _print_summary,
    _report_validation,
    _resolve_config_path,
    _rollback_partial,
    _run_smoke_test,
    _write_config,
)


@app.command()
def onboard(
    install_daemon: bool = typer.Option(
        None,
        "--install-daemon/--no-install-daemon",
        help=(
            "Register the daemon with the OS service manager. "
            "Omit to be prompted (default: yes)."
        ),
    ),
    config: str = typer.Option(
        None,
        "--config",
        "-c",
        help=(
            "Path to the voicegw.yaml to create or update. Default: "
            "~/.config/voicegateway/voicegw.yaml (matches v0.0.5)."
        ),
    ),
) -> None:
    """Five-question wizard: project, provider, API key, port, daemon."""
    config_path = _resolve_config_path(config)

    pre_existing_bytes: bytes | None = (
        config_path.read_bytes() if config_path.exists() else None
    )

    try:
        console.print("[bold]VoiceGateway onboarding[/bold]")
        console.print("Five questions. Press Ctrl+C any time to cancel.\n")

        # 1. Project name (default: default).
        project_name = typer.prompt("Project name", default="default")

        # 2. Provider (default: openai).
        provider = typer.prompt(
            f"Provider (one of: {', '.join(KNOWN_PROVIDERS)})",
            default="openai",
        )
        if provider not in KNOWN_PROVIDERS:
            console.print(
                f"[yellow]Unknown provider '{provider}'. Continuing; the YAML "
                "validator will catch typos at gateway construction time.[/yellow]"
            )

        api_key = typer.prompt(f"{provider} API key", hide_input=True)
        _report_validation(provider, api_key)

        # 4. Port (default: 8080).
        port = typer.prompt("Port for voicegw serve", default=8080, type=int)

        # 5. Install daemon (default: yes if the flag was omitted).
        if install_daemon is None:
            install_daemon = typer.confirm(
                "Install the background daemon?", default=True
            )

        _write_config(
            config_path,
            project_name=project_name,
            provider=provider,
            api_key=api_key,
            port=port,
        )

        console.print(f"\n[green]Wrote {config_path}[/green]", soft_wrap=True)

        if install_daemon:
            _install_daemon()

        _print_summary(
            config_path=config_path,
            project_name=project_name,
            provider=provider,
            port=port,
            daemon_installed=bool(install_daemon),
        )

        if typer.confirm("\nRun a smoke test now?", default=True):
            _run_smoke_test(config_path)

    except KeyboardInterrupt:
        _rollback_partial(config_path, pre_existing_bytes)
        console.print(
            "\n[yellow]Onboarding cancelled. Re-run `voicegw onboard` "
            "when ready.[/yellow]"
        )

        raise typer.Exit(130) from None
