"""``voicegw serve`` command."""

from __future__ import annotations

import typer

from voicegateway.cli._app import app, console
from voicegateway.utils.cli._shared import _load_gateway

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


@app.command(name="serve")
def serve_cmd(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
    host: str = typer.Option(
        None, "--host", help="Bind host (defaults to serve.host or 0.0.0.0)"
    ),
    port: int = typer.Option(
        None, "--port", help="Bind port (defaults to serve.port or 8080)"
    ),
) -> None:
    """Start the VoiceGateway HTTP API server under uvicorn."""
    try:
        import uvicorn
    except ImportError as e:
        console.print(
            "[red]Dashboard dependencies not installed. "
            "Run: pip install 'voicegateway[dashboard]'[/red]"
        )
        raise typer.Exit(1) from e

    gw = _load_gateway(config)
    host, port = _resolve_bind(getattr(gw.config, "serve", None), host, port)

    from voicegateway.core.auth import describe_auth, load_api_keys
    from voicegateway.server import build_app

    api_app = build_app(gw)
    console.print(f"[green]VoiceGateway API starting at http://{host}:{port}[/green]")
    console.print(f"[cyan]{describe_auth(load_api_keys(gw.config.auth))}[/cyan]")
    uvicorn.run(api_app, host=host, port=port)
