"""``voicegw serve`` command.

Carved out of voicegateway/cli/_legacy.py during the v0.1.0 section-2
refactor. Boots the FastAPI HTTP API server (``voicegateway.server``)
under uvicorn against the loaded gateway.

Bind precedence: explicit ``--host``/``--port`` flags win, then
``serve.host`` / ``serve.port`` from voicegw.yaml, then the v0.0.5
contract (0.0.0.0:8080). The middle path matters because the v0.1.0
daemon templates run bare ``voicegw serve``: without it the user's
wizard-selected port is silently ignored.
"""

from __future__ import annotations

import typer

from voicegateway.cli._app import app, console
from voicegateway.cli._helpers import _load_gateway

_DEFAULT_HOST = "0.0.0.0"
_DEFAULT_PORT = 8080


def _resolve_bind(
    gw_config_serve: object, host: str | None, port: int | None
) -> tuple[str, int]:
    """Resolve the (host, port) to bind on.

    Accepts ``serve_cfg`` as either a dict (the runtime ``GatewayConfig``
    dataclass keeps it raw) or a model-like object exposing ``.host`` /
    ``.port`` attributes. Tests construct both shapes.
    """

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
            # str(...) before int(...) keeps mypy happy (port_val is
            # typed object) and tolerates the handwritten-yaml case
            # where the value comes in as the string "8080".
            try:
                port = int(str(port_val))
            except ValueError:
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
    """Start the VoiceGateway HTTP API server."""
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
