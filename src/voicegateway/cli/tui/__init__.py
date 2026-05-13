"""VoiceGateway terminal UI package"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import typer

from voicegateway.cli._app import app as cli_app
from voicegateway.cli._app import console

if TYPE_CHECKING:  # pragma: no cover
    from voicegateway.cli.tui.app import TUIApp
    from voicegateway.core.config import GatewayConfig


def run(
    *,
    local: bool = False,
    url: str | None = None,
    token: str | None = None,
    poll: float | None = None,
    history_limit: int = 100,
    theme: str = "brand",
    config: str | None = None,
) -> None:
    """Launch the TUI programmatically."""

    from voicegateway.cli.tui.app import TUIApp
    from voicegateway.cli.tui.data.factory import make_client

    cfg = _try_load_config(config)
    resolved_url = url if url is not None else _url_from_config(cfg)

    resolved_db_path = _db_path_from_config(cfg) if local else None
    client = make_client(
        local=local,
        url=resolved_url,
        token=token,
        poll=poll,
        db_path=resolved_db_path,
    )
    app_instance = TUIApp(client=client, is_local=local)
    app_instance._history_limit = history_limit  # type: ignore[attr-defined]
    app_instance._theme = theme  # type: ignore[attr-defined]
    app_instance.run()


def _try_load_config(config_path: str | None) -> GatewayConfig | None:
    """Best-effort load of ``voicegw.yaml``."""
    try:
        from voicegateway.core.config import GatewayConfig

        return GatewayConfig.load(config_path)
    except Exception:  # noqa: BLE001
        return None


def _url_from_config(cfg: GatewayConfig | None) -> str:
    """Daemon URL fallback derived from ``serve.host`` / ``serve.port``."""
    if cfg is None:
        return "http://127.0.0.1:8080"
    serve = cfg.serve or {}
    host = serve.get("host", "0.0.0.0")
    port = serve.get("port", 8080)
    client_host = "127.0.0.1" if host == "0.0.0.0" else host
    return f"http://{client_host}:{port}"


def _db_path_from_config(cfg: GatewayConfig | None) -> str | None:
    """``cost_tracking.db_path`` from voicegw.yaml, or ``None``."""
    if cfg is None:
        return None
    value = cfg.cost_tracking.get("db_path")
    return str(value) if value else None


def _resolve_default_url(config_path: str | None) -> str:
    """Back-compat shim: load the config and derive the URL fallback."""
    return _url_from_config(_try_load_config(config_path))


@cli_app.command(name="tui")
def tui_cmd(
    local: bool = typer.Option(
        False,
        "--local",
        help=(
            "Use Local mode: read SQLite directly without going "
            "through the daemon. Useful for postmortem inspection "
            "when the daemon is down. Locked decision 5: --local "
            "wins regardless of whether the daemon is reachable."
        ),
    ),
    url: str = typer.Option(
        None,
        "--url",
        help=(
            "Daemon URL. Defaults to http://<serve.host>:<serve.port> "
            "from voicegw.yaml (with 0.0.0.0 rewritten to 127.0.0.1)."
        ),
    ),
    token: str = typer.Option(
        None,
        "--token",
        help=(
            "Bearer token for daemon write paths "
            "(e.g., the Providers tab's `t` test shortcut)."
        ),
    ),
    history_limit: int = typer.Option(
        100,
        "--history-limit",
        help="Initial row count for the Sessions and Logs tabs.",
    ),
    theme: str = typer.Option(
        "brand",
        "--theme",
        help="Color theme. `brand` is the v0.1.1 default.",
    ),
    poll: float = typer.Option(
        None,
        "--poll",
        help=(
            "Polling cadence in seconds. Default: 1.0 for Gateway "
            "mode, 5.0 for Local mode (locked decisions 1+2)."
        ),
    ),
    config: str = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to voicegw.yaml. Defaults to the standard search.",
    ),
) -> None:
    """Launch the four-tab terminal UI (Sessions / Costs / Logs / Providers)."""
    resolved_url = url if url is not None else _resolve_default_url(config)
    if not local and not _preflight_daemon_reachable(resolved_url, token):
        console.print(
            f"[red]Daemon at {resolved_url} is not reachable.[/red]\n"
            "Start it with `voicegw start` (after `voicegw onboard "
            "--install-daemon` if you have not yet), or run "
            "`voicegw tui --local` for read-only postmortem "
            "inspection of the SQLite call DB."
        )
        raise typer.Exit(1)
    try:
        run(
            local=local,
            url=resolved_url,
            token=token,
            poll=poll,
            history_limit=history_limit,
            theme=theme,
            config=config,
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Failed to launch TUI:[/red] {exc}")
        raise typer.Exit(1) from exc


def _preflight_daemon_reachable(
    url: str, token: str | None, timeout: float = 2.0
) -> bool:
    """Return ``True`` when the daemon at ``url`` answers ``/health``."""

    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = httpx.get(
            f"{url.rstrip('/')}/health",
            timeout=timeout,
            headers=headers,
        )
    except Exception:  # noqa: BLE001
        return False
    return response.status_code == 200


def __getattr__(name: str) -> Any:
    """Lazy module-level re-export of :class:`TUIApp` (PEP 562)."""
    if name == "TUIApp":
        from voicegateway.cli.tui.app import TUIApp as _TUIApp

        return _TUIApp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["TUIApp", "run", "tui_cmd"]
