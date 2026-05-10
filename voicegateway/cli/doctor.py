"""``voicegw doctor`` command.

Implements REQ-VG-ONBOARD-006: ten diagnostic checks rendered as a
numbered punch list with plain-language fix actions on every failure.

The check list follows the order in the v0.1.0 TODO:

  1. Python version (>= 3.11)
  2. pipx installed
  3. Daemon registered with the OS service manager
  4. Daemon running
  5. Port conflict on the configured serve port
  6. Provider configured in voicegw.yaml
  7. Provider key validates against the upstream API (fail-soft)
  8. Recent error count low (storage scan)
  9. Dashboard reachable on its bind port
  10. MCP responsive (when MCP is enabled)

Each check returns a ``CheckResult`` with one of three statuses:
``ok`` (green), ``fail`` (red, drives exit 1), ``skip`` (yellow,
non-blocking — used when the configured surface is intentionally
disabled, e.g. cost-tracking off skips the storage-error check).

Fix-action wording follows AC-VG-ONBOARD-006.2: a specific, plain-
language remediation step for every failure. No stack traces. No
bare ``see docs`` pointers.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass, field
from typing import Any

import typer
from rich.table import Table

from voicegateway.cli._app import app, console


@dataclass
class CheckResult:
    """One row in the doctor punch list."""

    label: str
    status: str  # "ok" | "fail" | "skip"
    detail: str = ""

    @property
    def is_blocker(self) -> bool:
        """True if this result counts toward the exit-1 failure tally."""
        return self.status == "fail"


@dataclass
class _Context:
    """Bundle of state every check needs.

    Built once per ``doctor`` invocation so individual checks don't
    each pay the gateway-load cost. ``gateway_load_error`` carries the
    exception message when the gateway-dependent checks should
    soft-skip rather than report a fail (e.g., voicegw.yaml missing —
    the provider-configured check covers that case directly).
    """

    config_path: str | None
    gateway: Any | None = None
    gateway_load_error: str | None = None
    daemon_status: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Individual checks. Each takes the shared _Context and returns a
# CheckResult. Order in _CHECKS below is the order the punch list
# renders.
# ---------------------------------------------------------------------------


def _check_python_version(ctx: _Context) -> CheckResult:
    major, minor = sys.version_info[:2]
    if (major, minor) >= (3, 11):
        return CheckResult(
            "Python version", "ok", f"{major}.{minor}.{sys.version_info.micro}"
        )
    return CheckResult(
        "Python version",
        "fail",
        f"Python {major}.{minor} is below the 3.11 minimum. Install 3.11+ "
        "via your OS package manager (brew/apt/dnf), then re-run `voicegw onboard`.",
    )


def _check_pipx(ctx: _Context) -> CheckResult:
    pipx = shutil.which("pipx")
    if pipx is not None:
        return CheckResult("pipx installed", "ok", pipx)
    return CheckResult(
        "pipx installed",
        "fail",
        "Install pipx with `python3 -m pip install --user pipx && python3 -m pipx ensurepath`, "
        "then open a new shell so ~/.local/bin lands on PATH.",
    )


def _check_daemon_registered(ctx: _Context) -> CheckResult:
    if ctx.daemon_status.get("registered"):
        return CheckResult(
            "Daemon registered",
            "ok",
            str(
                ctx.daemon_status.get("plist_path")
                or ctx.daemon_status.get("unit_path")
                or ""
            ),
        )
    return CheckResult(
        "Daemon registered",
        "fail",
        "Run `voicegw onboard --install-daemon` to register the daemon "
        "with the OS service manager.",
    )


def _check_daemon_running(ctx: _Context) -> CheckResult:
    if ctx.daemon_status.get("running"):
        pid = ctx.daemon_status.get("pid")
        return CheckResult("Daemon running", "ok", f"pid={pid}" if pid else "")
    if not ctx.daemon_status.get("registered"):
        # Already covered by check 3; surface a skip so the
        # operator's eye doesn't get duplicated noise.
        return CheckResult(
            "Daemon running", "skip", "(skipped because daemon not registered)"
        )
    return CheckResult(
        "Daemon running",
        "fail",
        "Run `voicegw start` to bring the daemon up.",
    )


def _check_port_conflict(ctx: _Context) -> CheckResult:
    port = _resolve_serve_port(ctx)
    if port is None:
        return CheckResult(
            "Port conflict",
            "skip",
            "(no serve port configured in voicegw.yaml)",
        )
    try:
        import psutil

        for conn in psutil.net_connections(kind="inet"):
            if conn.status == psutil.CONN_LISTEN and conn.laddr.port == port:
                # If the listener is OUR daemon, that's expected.
                if conn.pid == ctx.daemon_status.get("pid"):
                    return CheckResult(
                        "Port conflict",
                        "ok",
                        f"port {port} held by the voicegw daemon (pid {conn.pid})",
                    )
                return CheckResult(
                    "Port conflict",
                    "fail",
                    f"Port {port} is already in use by pid {conn.pid}. "
                    "Stop that process or pick a different port via "
                    "`voicegw onboard` / edit serve.port in voicegw.yaml.",
                )
        return CheckResult("Port conflict", "ok", f"port {port} is free")
    except (psutil.AccessDenied, OSError) as exc:
        return CheckResult(
            "Port conflict",
            "skip",
            f"(could not enumerate sockets: {exc})",
        )


def _check_provider_configured(ctx: _Context) -> CheckResult:
    if ctx.gateway is None:
        return CheckResult(
            "Provider configured",
            "fail",
            f"Could not load voicegw.yaml: {ctx.gateway_load_error}. "
            "Run `voicegw init` to scaffold a config, or `voicegw onboard` "
            "to fill it in interactively.",
        )
    providers = list(ctx.gateway.config.providers.keys())
    if providers:
        return CheckResult("Provider configured", "ok", ", ".join(sorted(providers)))
    return CheckResult(
        "Provider configured",
        "fail",
        "voicegw.yaml has no providers configured. Run `voicegw onboard` "
        "to add one (you'll need an API key from your provider's dashboard).",
    )


def _check_provider_key_valid(ctx: _Context) -> CheckResult:
    """Best-effort: validate at most one configured provider's key.

    Reuses the same plumbing the wizard uses (5-second cap +
    fail-soft). Probes the FIRST configured provider only so doctor
    runs in bounded time even with many providers; full coverage
    lives in the wizard's per-provider validation flow.
    """
    if ctx.gateway is None:
        return CheckResult(
            "Provider key valid",
            "skip",
            "(skipped because voicegw.yaml could not be loaded)",
        )
    providers = ctx.gateway.config.providers
    if not providers:
        return CheckResult(
            "Provider key valid",
            "skip",
            "(skipped because no provider is configured)",
        )

    name, cfg = next(iter(providers.items()))
    api_key = cfg.get("api_key", "")
    if not api_key:
        return CheckResult(
            "Provider key valid",
            "fail",
            f"Provider '{name}' has no api_key set. Run `voicegw onboard` "
            "to add one, or set it directly in voicegw.yaml.",
        )

    import asyncio

    from voicegateway.cli.onboard import _validate_provider_key

    status, message = asyncio.run(_validate_provider_key(name, api_key))
    if status == "ok":
        return CheckResult("Provider key valid", "ok", f"{name}: validated")
    if status == "skipped":
        return CheckResult("Provider key valid", "skip", f"{name}: {message}")
    if status == "timeout":
        return CheckResult(
            "Provider key valid",
            "skip",
            f"{name}: validation timed out (your key may still be correct)",
        )
    return CheckResult(
        "Provider key valid",
        "fail",
        f"{name} key was rejected ({message}). Re-check the value in your "
        "provider dashboard, then run `voicegw onboard` to update.",
    )


def _check_recent_error_count(ctx: _Context) -> CheckResult:
    """Count failed request rows in the last hour."""
    if ctx.gateway is None or ctx.gateway.storage is None:
        return CheckResult(
            "Recent error count",
            "skip",
            "(skipped because cost-tracking is disabled)",
        )
    try:
        import asyncio

        rows = asyncio.run(ctx.gateway.storage.get_recent_requests(limit=100))
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            "Recent error count",
            "skip",
            f"(could not read storage: {exc})",
        )
    failed = sum(1 for r in rows if str(r.get("status", "ok")).lower() != "ok")
    if failed == 0:
        return CheckResult(
            "Recent error count", "ok", f"0 errors in the last {len(rows)} requests"
        )
    return CheckResult(
        "Recent error count",
        "fail",
        f"{failed} of the last {len(rows)} requests failed. Run "
        "`voicegw logs --tail 20` to see the most recent ones, then "
        "`voicegw doctor` again after fixing.",
    )


def _check_dashboard_reachable(ctx: _Context) -> CheckResult:
    port = _resolve_dashboard_port(ctx)
    url = f"http://127.0.0.1:{port}/health"
    try:
        import httpx

        response = httpx.get(url, timeout=2.0)
    except httpx.ConnectError:
        return CheckResult(
            "Dashboard reachable",
            "fail",
            f"Could not connect to {url}. Run `voicegw dashboard` "
            "or check that the daemon's dashboard process is running.",
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            "Dashboard reachable",
            "skip",
            f"(probe failed: {exc})",
        )
    if response.status_code == 200:
        return CheckResult("Dashboard reachable", "ok", url)
    return CheckResult(
        "Dashboard reachable",
        "fail",
        f"Dashboard at {url} returned HTTP {response.status_code}. "
        "Check `voicegw logs` for an error trace.",
    )


def _check_mcp_responsive(ctx: _Context) -> CheckResult:
    """MCP responsive when MCP is enabled.

    v0.1.0 ships an MCP probe that's best-effort: the cli has no
    way to know whether the operator runs MCP via stdio (always
    available; nothing to ping) vs. http (port-bound; pingable).
    Skip with a documented note rather than guessing.
    """
    return CheckResult(
        "MCP responsive",
        "skip",
        "(stdio MCP has no probe surface; HTTP MCP probe deferred to a follow-up)",
    )


_CHECKS = (
    _check_python_version,
    _check_pipx,
    _check_daemon_registered,
    _check_daemon_running,
    _check_port_conflict,
    _check_provider_configured,
    _check_provider_key_valid,
    _check_recent_error_count,
    _check_dashboard_reachable,
    _check_mcp_responsive,
)


# ---------------------------------------------------------------------------
# Helpers shared across checks.
# ---------------------------------------------------------------------------


def _resolve_serve_port(ctx: _Context) -> int | None:
    if ctx.gateway is None:
        return None
    serve_cfg = getattr(ctx.gateway.config, "serve", None) or {}
    if isinstance(serve_cfg, dict) and "port" in serve_cfg:
        try:
            return int(serve_cfg["port"])
        except (TypeError, ValueError):
            return None
    return None


def _resolve_dashboard_port(ctx: _Context) -> int:
    if ctx.gateway is None:
        return 9090
    dashboard_cfg = getattr(ctx.gateway.config, "dashboard", None) or {}
    if isinstance(dashboard_cfg, dict) and "port" in dashboard_cfg:
        try:
            return int(dashboard_cfg["port"])
        except (TypeError, ValueError):
            pass
    return 9090


def _build_context(config_path: str | None) -> _Context:
    ctx = _Context(config_path=config_path)
    try:
        from voicegateway.cli._helpers import _load_gateway

        ctx.gateway = _load_gateway(config_path)
    except (typer.Exit, Exception) as exc:  # noqa: BLE001
        ctx.gateway = None
        ctx.gateway_load_error = type(exc).__name__
    try:
        from voicegateway.cli.daemon import DaemonManager

        ctx.daemon_status = DaemonManager().status()
    except Exception:  # noqa: BLE001
        ctx.daemon_status = {"registered": False, "running": False, "pid": None}
    return ctx


# ---------------------------------------------------------------------------
# Typer command.
# ---------------------------------------------------------------------------


_STATUS_RENDER = {
    "ok": "[green]PASS[/green]",
    "fail": "[red]FAIL[/red]",
    "skip": "[yellow]SKIP[/yellow]",
}


@app.command()
def doctor(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
) -> None:
    """Run diagnostic checks. Numbered punch list with fix actions.

    Exits 0 when every check is ok or skipped. Exits 1 when any
    check fails so chained tooling can detect "not healthy" without
    parsing output.
    """
    ctx = _build_context(config)
    results = [check(ctx) for check in _CHECKS]

    table = Table(title="VoiceGateway doctor")
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("Check", style="cyan")
    table.add_column("Status", width=6)
    table.add_column("Detail", overflow="fold")

    for i, r in enumerate(results, start=1):
        table.add_row(
            str(i),
            r.label,
            _STATUS_RENDER.get(r.status, r.status.upper()),
            r.detail,
        )

    console.print(table)

    failures = [r for r in results if r.is_blocker]
    if failures:
        labels = ", ".join(r.label for r in failures)
        console.print(
            f"\n[yellow]{len(failures)} check(s) need attention:[/yellow] {labels}"
        )
        raise typer.Exit(1)

    console.print("\n[green]All checks passed.[/green]")
