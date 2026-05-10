"""``voicegw serve`` command.

Carved out of voicegateway/cli/_legacy.py during the v0.1.0 section-2
refactor. Boots the FastAPI HTTP API server (``voicegateway.server``)
under uvicorn against the loaded gateway. Listens on the host/port
the operator passes; defaults are the v0.0.5 contract (0.0.0.0:8080).
"""

from __future__ import annotations

import typer

from voicegateway.cli._app import app, console
from voicegateway.cli._helpers import _load_gateway


@app.command(name="serve")
def serve_cmd(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
    host: str = typer.Option("0.0.0.0", "--host", help="Bind host"),
    port: int = typer.Option(8080, "--port", help="Bind port"),
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
    from voicegateway.core.auth import describe_auth, load_api_keys
    from voicegateway.server import build_app

    api_app = build_app(gw)
    console.print(f"[green]VoiceGateway API starting at http://{host}:{port}[/green]")
    console.print(f"[cyan]{describe_auth(load_api_keys(gw.config.auth))}[/cyan]")
    uvicorn.run(api_app, host=host, port=port)
