"""Helpers for ``voicegateway.cli.onboard_cli``."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml
from rich.table import Table

from voicegateway.cli._app import console
from voicegateway.core.constants import SMOKE_TEST_TIMEOUT_S


def _resolve_config_path(explicit: str | None) -> Path:
    """Use the explicit path if provided, else the documented default."""
    if explicit:
        return Path(explicit)
    return Path.home() / ".config" / "voicegateway" / "voicegw.yaml"


def _write_config(
    path: Path,
    *,
    project_name: str,
    port: int,
    db_path: str | None = None,
) -> None:
    """Merge wizard input into the target voicegw.yaml.

    Framework-agnostic: VoiceGateway meters the native provider instances you
    attach() in your agent, so no provider or API key is written here.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, Any] = {}
    if path.exists():
        loaded = yaml.safe_load(path.read_text()) or {}
        if isinstance(loaded, dict):
            existing = loaded

    projects = existing.setdefault("projects", {})
    projects.setdefault(project_name, {})
    if "name" not in projects[project_name]:
        projects[project_name]["name"] = project_name.replace("-", " ").title()

    # The gateway resolves the active project from this top-level key.
    existing["default_project"] = project_name

    cost_tracking = existing.setdefault("cost_tracking", {})
    cost_tracking["enabled"] = True
    if db_path:
        cost_tracking["db_path"] = db_path

    serve_section = existing.setdefault("serve", {})
    serve_section["port"] = port

    path.write_text(yaml.dump(existing, default_flow_style=False, sort_keys=False))


def _install_daemon(config_path: Path) -> None:
    """Register the daemon so it serves ``config_path``."""
    from voicegateway.cli.daemon import DaemonManager

    console.print("\nInstalling background daemon...")
    DaemonManager().install(config_path=str(config_path))
    console.print("[green]Daemon installed and started.[/green]")


def _print_summary(
    *,
    config_path: Path,
    project_name: str,
    port: int,
    daemon_installed: bool,
) -> None:
    """Render the end-of-wizard summary + the agent integration snippet."""
    table = Table(title="Onboarding complete", show_header=False, box=None)
    table.add_column(style="dim")
    table.add_column()

    table.add_row("Project", project_name)
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

    # The integration snippet is what actually meters your agent.
    console.print("\n[bold]Add one line to your agent:[/bold]")
    console.print(
        "    [cyan]import voicegateway[/cyan]\n"
        f'    [cyan]voicegateway.attach(session, project="{project_name}")[/cyan]'
    )
    console.print(
        "\n[dim]Fleet mode (optional): point agents at a shared collector with[/dim]\n"
        "[dim]    VOICEGW_COLLECTOR_URL=<collector-base-url>[/dim]\n"
        "[dim]    VOICEGW_API_KEY=<ingest key>[/dim]"
    )

    dashboard_url = f"http://127.0.0.1:{port}"
    console.print(f"\n[bold]Dashboard:[/bold] {dashboard_url}")
    if daemon_installed:
        console.print(
            "The daemon is serving it. Open with [cyan]voicegw dashboard[/cyan] "
            "(launches your browser), or visit the URL directly. Use "
            "[cyan]voicegw status[/cyan] / [cyan]voicegw doctor[/cyan] for "
            "diagnostics, [cyan]voicegw stop[/cyan] to bring the daemon down."
        )
    else:
        console.print(
            "Nothing is serving it yet. Start the server with "
            "[cyan]voicegw serve[/cyan] (runs in the foreground), then open "
            "[cyan]voicegw dashboard[/cyan] or visit the URL. Use "
            "[cyan]voicegw doctor[/cyan] for diagnostics."
        )


def _run_check(config_path: Path) -> None:
    """Spawn ``voicegw check --config <path>`` and report.

    Subprocess (not in-process) so the check runs with a fresh session ContextVar
    and no gateway-singleton bleed.
    """
    voicegw = shutil.which("voicegw")
    if voicegw is None:
        console.print(
            "[yellow]Could not find 'voicegw' on PATH; skipping check. "
            "Run `voicegw check` once your shell sees the binary.[/yellow]"
        )
        return

    console.print(
        f"\n[bold]Running check...[/bold] (cap: {int(SMOKE_TEST_TIMEOUT_S)}s)"
    )

    try:
        result = subprocess.run(
            [voicegw, "check", "--config", str(config_path)],
            capture_output=True,
            text=True,
            timeout=SMOKE_TEST_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        console.print(
            f"[yellow]Check timed out after {int(SMOKE_TEST_TIMEOUT_S)}s. "
            f"Run `voicegw check --config {config_path}` manually to see how "
            "far it got.[/yellow]"
        )
        return

    if result.returncode == 0:
        console.print(
            "[green]Check passed.[/green] A synthetic request landed in storage."
        )
        return

    console.print(
        f"[yellow]Check failed (exit {result.returncode}). "
        f"Run `voicegw check --config {config_path}` to see the full "
        "report.[/yellow]"
    )
    # `check` prints its report table to stdout; the tail is the actionable bit.
    if result.stdout:
        tail = "\n".join(result.stdout.strip().splitlines()[-6:])
        if tail:
            console.print(f"[dim]{tail}[/dim]")


def _rollback_partial(config_path: Path, pre_existing_bytes: bytes | None) -> None:
    """Restore the pre-wizard config state."""
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
