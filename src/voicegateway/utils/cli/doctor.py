"""Helpers for ``voicegateway.cli.doctor_cli``."""

from __future__ import annotations

import asyncio
import shutil
import sys
from dataclasses import dataclass, field
from typing import Any

import typer

from voicegateway.core.constants import VALIDATION_TIMEOUT_S


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
    """Bundle of state every check needs."""

    config_path: str | None
    gateway: Any | None = None
    gateway_load_error: str | None = None
    daemon_status: dict[str, Any] = field(default_factory=dict)


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
    uv = shutil.which("uv")
    if uv is not None:
        return CheckResult(
            "pipx installed", "skip", f"(not needed: installed via uv at {uv})"
        )
    return CheckResult(
        "pipx installed",
        "fail",
        "Install pipx with `python3 -m pip install --user pipx && python3 -m pipx ensurepath`, "
        "then open a new shell so ~/.local/bin lands on PATH. (Or use uv.)",
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
    # Framework-agnostic: no provider is required. VoiceGateway meters the
    # native instances you build in your agent and pass to attach() / guard().
    return CheckResult(
        "Provider configured",
        "skip",
        "no provider configured (not required): VoiceGateway meters the native "
        "instances you attach() in your agent.",
    )


def _check_provider_key_valid(ctx: _Context) -> CheckResult:
    """Best-effort: validate at most one configured provider's key."""
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
            f"Provider '{name}' has no api_key set. Set it directly in "
            "voicegw.yaml under providers.",
        )

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
        "provider dashboard, then update it in voicegw.yaml.",
    )


async def _validate_provider_key(provider: str, api_key: str) -> tuple[str, str | None]:
    """Drive a configured provider's ``health_check`` under a short timeout.

    Legacy provider-validation path used by the doctor "Provider key valid"
    check. In the framework-agnostic model most configs carry no ``providers:``
    block, so this only runs when the operator has set one explicitly.
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
            instance.health_check(), timeout=VALIDATION_TIMEOUT_S
        )
    except TimeoutError:
        return "timeout", None
    except Exception as exc:  # noqa: BLE001
        return "failed", f"{type(exc).__name__}: {exc}"

    if ok:
        return "ok", None
    return "failed", "authentication declined"


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
    # Success sentinels: a request is only "failed" if it is none of these.
    # The stored success status is "success" (not "ok"); "fallback" succeeded via
    # a fallback provider. Counting anything != "ok" would flag every real
    # successful request as an error.
    ok_statuses = {"ok", "success", "fallback"}
    failed = sum(
        1 for r in rows if str(r.get("status", "success")).lower() not in ok_statuses
    )
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
    # The server (daemon or `voicegw serve`) serves the dashboard at the serve
    # port; fall back to a separate dashboard.port / 9090 only if serve is unset.
    port = _resolve_serve_port(ctx) or _resolve_dashboard_port(ctx)
    url = f"http://127.0.0.1:{port}/health"
    try:
        import httpx

        response = httpx.get(url, timeout=2.0)
    except httpx.ConnectError:
        return CheckResult(
            "Dashboard reachable",
            "skip",
            f"(no listener on {url}; the daemon does not auto-start "
            "`voicegw dashboard`. Run `voicegw dashboard` in a "
            "separate process if you want the web UI.)",
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
    """MCP responsive when MCP is enabled."""
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
        from voicegateway.utils.cli._shared import _load_gateway

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
