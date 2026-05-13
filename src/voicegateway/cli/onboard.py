"""``voicegw onboard`` wizard."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Any

import typer
import yaml

from voicegateway.cli._app import app, console
from voicegateway.core.constants import (
    DEFAULT_DASHBOARD_PORT,
    KNOWN_PROVIDERS,
    SMOKE_TEST_TIMEOUT_S,
    VALIDATION_TIMEOUT_S,
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


def _resolve_config_path(explicit: str | None) -> Path:
    """Use the explicit path if provided, else the v0.0.5 default."""
    if explicit:
        return Path(explicit)
    return Path.home() / ".config" / "voicegateway" / "voicegw.yaml"


def _write_config(
    path: Path,
    *,
    project_name: str,
    provider: str,
    api_key: str,
    port: int,
) -> None:
    """Merge wizard input into the target voicegw.yaml."""
    path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, Any] = {}
    if path.exists():
        loaded = yaml.safe_load(path.read_text()) or {}
        if isinstance(loaded, dict):
            existing = loaded

    providers = existing.setdefault("providers", {})
    providers[provider] = {"api_key": api_key}

    projects = existing.setdefault("projects", {})
    projects.setdefault(project_name, {})
    if "name" not in projects[project_name]:
        projects[project_name]["name"] = project_name.replace("-", " ").title()

    existing["default_project"] = project_name

    cost_tracking = existing.setdefault("cost_tracking", {})
    cost_tracking["enabled"] = True

    serve_section = existing.setdefault("serve", {})
    serve_section["port"] = port

    path.write_text(yaml.dump(existing, default_flow_style=False, sort_keys=False))


def _install_daemon() -> None:
    """Register the daemon."""
    from voicegateway.cli.daemon import DaemonManager

    console.print("\nInstalling background daemon...")
    DaemonManager().install()
    console.print("[green]Daemon installed and started.[/green]")


def _print_summary(
    *,
    config_path: Path,
    project_name: str,
    provider: str,
    port: int,
    daemon_installed: bool,
) -> None:
    """Render the documented end-of-wizard summary."""
    from rich.table import Table

    table = Table(title="Onboarding complete", show_header=False, box=None)
    table.add_column(style="dim")
    table.add_column()

    table.add_row("Project", project_name)
    table.add_row("Provider", provider)
    table.add_row("Serve port", str(port))
    table.add_row(
        "Daemon",
        "[green]installed and started[/green]"
        if daemon_installed
        else "[yellow]not installed (run `voicegw onboard --install-daemon` to add)[/yellow]",
    )

    console.print()
    console.print(table)
    console.print(f"\n[dim]Config:[/dim] {config_path}", soft_wrap=True)

    dashboard_url = f"http://127.0.0.1:{DEFAULT_DASHBOARD_PORT}"
    console.print(f"\n[bold]Dashboard:[/bold] {dashboard_url}")
    console.print(
        "Open that URL after a request lands; "
        "use [cyan]voicegw status[/cyan] / "
        "[cyan]voicegw doctor[/cyan] for diagnostics."
    )


def _run_smoke_test(config_path: Path) -> None:
    """Spawn ``voicegw smoke-test --config <path>`` and report."""

    voicegw = shutil.which("voicegw")
    if voicegw is None:
        console.print(
            "[yellow]Could not find 'voicegw' on PATH; skipping smoke test. "
            "Run `voicegw smoke-test` once your shell sees the binary.[/yellow]"
        )
        return

    console.print(
        f"\n[bold]Running smoke test...[/bold] (cap: {int(SMOKE_TEST_TIMEOUT_S)}s)"
    )

    try:
        result = subprocess.run(
            [voicegw, "smoke-test", "--config", str(config_path)],
            capture_output=True,
            text=True,
            timeout=SMOKE_TEST_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        console.print(
            f"[yellow]Smoke test timed out after {int(SMOKE_TEST_TIMEOUT_S)}s. "
            f"Run `voicegw smoke-test --config {config_path}` manually to "
            "see how far it got.[/yellow]"
        )
        return

    if result.returncode == 0:
        console.print("[green]Smoke test passed.[/green] First call landed in storage.")
        return

    console.print(
        f"[yellow]Smoke test failed (exit {result.returncode}). "
        f"Run `voicegw smoke-test --config {config_path}` to see the "
        "full report.[/yellow]"
    )
    # Last few lines of stderr are usually the actionable bit.
    if result.stderr:
        tail = "\n".join(result.stderr.strip().splitlines()[-5:])
        if tail:
            console.print(f"[dim]{tail}[/dim]")


def _rollback_partial(config_path: Path, pre_existing_bytes: bytes | None) -> None:
    """Restore the pre-wizard config state.

    Three cases:
      1. config didn't exist before AND nothing was written: nothing
         to do.
      2. config didn't exist before AND a partial write happened:
         remove the partial file.
      3. config existed before: restore the snapshot (byte-for-byte
         atomic).

    OSError is swallowed because the rollback is best-effort: a
    failure here loses some idempotency but should not raise from
    inside an except block (would mask the original KeyboardInterrupt).
    """
    if not config_path.exists():
        return
    try:
        if pre_existing_bytes is not None:
            config_path.write_bytes(pre_existing_bytes)
        else:
            config_path.unlink()
    except OSError as exc:
        console.print(
            f"[dim]Could not roll back {config_path}: {exc}. Inspect manually.[/dim]"
        )


def _report_validation(provider: str, api_key: str) -> None:
    """Run the provider key validation and surface the result."""
    status, message = asyncio.run(_validate_provider_key(provider, api_key))
    if status == "ok":
        console.print("[green]Provider key validated.[/green]")
    elif status == "skipped":
        console.print(f"[dim]Skipping live validation: {message}[/dim]")
    elif status == "timeout":
        console.print(
            "[yellow]Provider validation timed out: your key may still "
            "be correct; continuing.[/yellow]"
        )
    else:  # "failed"
        console.print(
            f"[yellow]Provider validation failed ({message}). "
            "Continuing; run `voicegw doctor` to inspect.[/yellow]"
        )


async def _validate_provider_key(provider: str, api_key: str) -> tuple[str, str | None]:
    """Drive the v0.0.5 provider health_check path under a 5-second timeout."""
    from voicegateway.core.registry import _PROVIDER_REGISTRY, create_provider

    if provider not in _PROVIDER_REGISTRY:
        return "skipped", f"unknown provider name '{provider}'"

    try:
        instance = create_provider(provider, {"api_key": api_key})
    except ImportError as exc:
        return "skipped", f"plugin not installed ({exc})"
    except Exception as exc:  # noqa: BLE001
        return "failed", f"{type(exc).__name__}: {exc}"

    try:
        ok = await asyncio.wait_for(
            instance.health_check(), timeout=VALIDATION_TIMEOUT_S
        )
    except TimeoutError:
        return "timeout", None
    except Exception as exc:  # noqa: BLE001
        return "failed", f"{type(exc).__name__}: {exc}"

    if ok:
        return "ok", None
    return "failed", "authentication declined"
