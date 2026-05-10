"""``voicegw onboard`` wizard.

Five Typer prompts that produce a working voicegw.yaml plus an
optionally-registered daemon. Implements REQ-VG-ONBOARD-002 from
the v0.1.0 Refinery.

Iteration scope (current):
  - Prompts (project name / provider / API key / port / daemon).
  - Real-time provider key validation with a 5-second timeout.
    Reuses the v0.0.5 ``vg_test_provider_key`` health-check
    code path: instantiate the provider via ``create_provider``,
    call ``await health_check()`` under
    ``asyncio.wait_for(..., timeout=5.0)``. Fail-soft on timeout
    so a slow network does not block onboarding.
  - Config file write that merges into an existing voicegw.yaml.
  - Daemon registration via DaemonManager.

Subsequent v0.1.0 commits layer on:
  - Clean Ctrl+C cancellation with partial-state cleanup
    (REQ-VG-ONBOARD-002.4).
  - Final summary showing exactly what was configured
    (AC-VG-ONBOARD-002.3).
  - Smoke-test offering at the end (REQ-VG-ONBOARD-005).
  - Additional tests for each scenario above.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import typer
import yaml

from voicegateway.cli._app import app, console

# The cloud providers the v0.0.5 inference module supports. Wizard
# offers OpenAI as the default per design.md decision (most common
# starting point); other names are accepted but not validated here
# (downstream YAML schema does that).
_KNOWN_PROVIDERS = (
    "openai",
    "deepgram",
    "anthropic",
    "groq",
    "cartesia",
    "elevenlabs",
    "assemblyai",
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

    # Snapshot the existing config (if any) so a Ctrl+C mid-wizard
    # restores the pre-wizard state byte-for-byte. The "remove any
    # partial config files" half of REQ-VG-ONBOARD-002.4: if no file
    # existed before, ``pre_existing_bytes`` is None and the cleanup
    # path deletes the partial write outright.
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
            f"Provider (one of: {', '.join(_KNOWN_PROVIDERS)})",
            default="openai",
        )
        if provider not in _KNOWN_PROVIDERS:
            console.print(
                f"[yellow]Unknown provider '{provider}'. Continuing; the YAML "
                "validator will catch typos at gateway construction time.[/yellow]"
            )

        # 3. API key (no default, hidden input). Validate with a
        # 5-second timeout per REQ-VG-ONBOARD-002.2; fail soft on
        # timeout so a slow network does not block onboarding.
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
        # ``soft_wrap=True`` keeps the path on a single line even when
        # the tmp/test paths are longer than the default console width;
        # otherwise Rich folds long paths mid-segment and the resulting
        # output is unreadable in CI logs.
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

    except KeyboardInterrupt:
        _rollback_partial(config_path, pre_existing_bytes)
        console.print(
            "\n[yellow]Onboarding cancelled. Re-run `voicegw onboard` "
            "when ready.[/yellow]"
        )
        # 130 = 128 + SIGINT(2). Standard convention for shells; what
        # interactive tools should return so chained tooling can
        # detect "the user said no" vs. a hard failure.
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
    """Merge wizard input into the target voicegw.yaml.

    Existing top-level keys are preserved so the wizard can run
    twice without clobbering hand-edited config (idempotency
    requirement, AC-VG-ONBOARD-002.5). The merge strategy:

      - ``providers`` dict gets a new (or replaced) entry under
        ``provider`` with the API key.
      - ``projects`` dict gets a (or replaced) entry under
        ``project_name``.
      - ``default_project`` is set to ``project_name``.
      - ``cost_tracking.enabled`` is set to True so the wizard's
        end-state has cost tracking on by default.
      - ``serve.port`` records the chosen port. v0.0.5's
        ``voicegw serve`` reads its port from the CLI flag rather
        than this key, so the value is captured here for future
        plumbing rather than load-bearing today.

    The file is written in YAML with sort_keys=False so the
    layout follows the order the wizard cares about.
    """
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
    """Register the daemon. Lazy-imports DaemonManager so a wizard
    run that declines installation never touches the OS-specific
    backend code (and so unit tests that exercise the prompts
    without daemon registration do not need to mock subprocess).
    """
    from voicegateway.cli.daemon import DaemonManager

    console.print("\nInstalling background daemon...")
    DaemonManager().install()
    console.print("[green]Daemon installed and started.[/green]")


# Default dashboard port matches v0.0.5's ``voicegw dashboard`` flag
# default. Surfaced in the summary so the user can paste the URL
# straight into their browser without a second cli invocation.
_DEFAULT_DASHBOARD_PORT = 9090


def _print_summary(
    *,
    config_path: Path,
    project_name: str,
    provider: str,
    port: int,
    daemon_installed: bool,
) -> None:
    """Render the documented end-of-wizard summary.

    Implements AC-VG-ONBOARD-002.3: the user sees, byte-for-byte,
    exactly what the wizard configured plus the URL they should
    open and the next-step commands. Printed via a Rich table so
    the rendering is consistent with ``voicegw status`` and
    ``voicegw doctor``.
    """
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
    # Config-file path lives below the table so Rich's default
    # column truncation cannot fold a long tmp/CI path mid-segment.
    # ``soft_wrap=True`` keeps the path on one line.
    console.print(f"\n[dim]Config:[/dim] {config_path}", soft_wrap=True)

    dashboard_url = f"http://127.0.0.1:{_DEFAULT_DASHBOARD_PORT}"
    console.print(f"\n[bold]Dashboard:[/bold] {dashboard_url}")
    console.print(
        "Open that URL after a request lands; "
        "use [cyan]voicegw status[/cyan] / "
        "[cyan]voicegw doctor[/cyan] for diagnostics."
    )


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


# Five-second cap on the live health probe. Slow networks return a
# soft warning rather than blocking the wizard; this matches design.md
# section-8 risk mitigation for the 60-second clock.
_VALIDATION_TIMEOUT_S = 5.0


def _report_validation(provider: str, api_key: str) -> None:
    """Run the provider key validation and surface the result.

    Three paths the user sees:

      - ok: a green "validated" line, then the prompt continues.
      - timeout: a yellow soft-warn ("validation timed out: your
        key may still be correct; continuing"). REQ-VG-ONBOARD-002.2.
      - auth/network failure: a yellow warning with the underlying
        error and a pointer to ``voicegw doctor`` for a deeper
        check. The wizard continues so a typo gets caught at
        first call rather than mid-wizard, where the user has
        less leverage to fix it.

    Unknown providers (anything outside ``_PROVIDER_REGISTRY``) and
    missing plugin extras short-circuit to "skipped"; the YAML
    schema validator catches typos at gateway construction time.
    """
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
    """Drive the v0.0.5 provider health_check path under a 5-second
    timeout.

    Returns one of:
      ("ok", None)                  - health_check returned True
      ("failed", "<error msg>")     - health_check returned False or raised
      ("timeout", None)             - hit the 5-second cap
      ("skipped", "<reason>")       - unknown provider name or
                                      plugin not installed; the YAML
                                      validator + voicegw doctor cover
                                      the rest.

    Reuses the same ``create_provider`` + ``health_check`` plumbing
    that ``vg_test_provider_key`` (MCP) uses, so a regression in
    the underlying provider plugins lights up both surfaces.
    """
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
            instance.health_check(), timeout=_VALIDATION_TIMEOUT_S
        )
    except TimeoutError:
        return "timeout", None
    except Exception as exc:  # noqa: BLE001
        return "failed", f"{type(exc).__name__}: {exc}"

    if ok:
        return "ok", None
    return "failed", "authentication declined"
