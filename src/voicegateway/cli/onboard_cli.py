"""``voicegw onboard`` wizard."""

from __future__ import annotations

import typer

from voicegateway.cli._app import app, console
from voicegateway.cli.base_cli import BaseCli
from voicegateway.utils.cli.onboard import (
    _install_daemon,
    _print_summary,
    _resolve_config_path,
    _rollback_partial,
    _run_check,
    _write_config,
)

_cli = BaseCli()


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
            "~/.config/voicegateway/voicegw.yaml."
        ),
    ),
) -> None:
    """Four-question wizard: project, storage, port, daemon.

    VoiceGateway is framework-agnostic: it meters the native provider instances
    you build in your agent (via ``attach()``), so onboarding configures storage
    and the daemon, not a provider or key.
    """
    config_path = _resolve_config_path(config)

    pre_existing_bytes: bytes | None = (
        config_path.read_bytes() if config_path.exists() else None
    )

    try:
        console.print("[bold]VoiceGateway onboarding[/bold]")
        console.print("Four questions. Press Ctrl+C any time to cancel.\n")

        # 1. Project name (default: default).
        project_name = typer.prompt("Project name", default="default")

        # 2. Storage: SQLite db path (blank uses the default path).
        db_path = typer.prompt(
            "Storage: SQLite db path (blank = ~/.config/voicegateway/voicegw.db)",
            default="",
            show_default=False,
        ).strip()

        # 3. Port (default: 8080).
        port = typer.prompt("Port for voicegw serve", default=8080, type=int)

        # 4. Install daemon (default: yes if the flag was omitted).
        if install_daemon is None:
            install_daemon = typer.confirm(
                "Install the background daemon?", default=True
            )

        _write_config(
            config_path,
            project_name=project_name,
            port=port,
            db_path=db_path or None,
        )

        _cli.success(f"\nWrote {config_path}")

        daemon_installed = False
        if install_daemon:
            # A daemon-install failure (e.g. launchctl bootstrap in a terminal
            # without a usable GUI session) must not abort onboarding: the
            # config is already written. Warn and continue. KeyboardInterrupt is
            # a BaseException, so it still propagates to the rollback handler.
            try:
                _install_daemon(config_path)
                daemon_installed = True
            except Exception as exc:  # noqa: BLE001
                _cli.warn(
                    f"\nDaemon install failed: {exc}\n"
                    "Your config was written. Start the daemon later with "
                    "`voicegw start`, or re-run with --no-install-daemon to skip it."
                )

        _print_summary(
            config_path=config_path,
            project_name=project_name,
            port=port,
            daemon_installed=daemon_installed,
        )

        if typer.confirm("\nRun a check now?", default=True):
            _run_check(config_path)

    except KeyboardInterrupt:
        _rollback_partial(config_path, pre_existing_bytes)
        _cli.warn("\nOnboarding cancelled. Re-run `voicegw onboard` when ready.")
        raise typer.Exit(130) from None
