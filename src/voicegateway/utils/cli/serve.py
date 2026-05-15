"""Helpers for ``voicegateway.cli.serve_cli``."""

from __future__ import annotations

from voicegateway.cli._app import console

_DEFAULT_HOST = "0.0.0.0"
_DEFAULT_PORT = 8080
_MIN_PORT = 1
_MAX_PORT = 65535


def _resolve_bind(
    gw_config_serve: object, host: str | None, port: int | None
) -> tuple[str, int]:
    """Resolve the (host, port) to bind on."""

    def _from_serve(key: str) -> object | None:
        if gw_config_serve is None:
            return None
        if isinstance(gw_config_serve, dict):
            return gw_config_serve.get(key)
        return getattr(gw_config_serve, key, None)

    if host is None:
        host_val = _from_serve("host")
        host = str(host_val) if host_val else _DEFAULT_HOST

    if port is None:
        port_val = _from_serve("port")
        if port_val is None:
            port = _DEFAULT_PORT
        else:
            try:
                port = int(str(port_val))
            except ValueError:
                port = _DEFAULT_PORT

    if not _MIN_PORT <= port <= _MAX_PORT:
        console.print(
            f"[yellow]Serve port {port} is outside {_MIN_PORT}..{_MAX_PORT}; "
            f"falling back to {_DEFAULT_PORT}.[/yellow]"
        )
        port = _DEFAULT_PORT

    return host, port
