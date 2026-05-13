"""``voicegw serve`` command."""

from __future__ import annotations

import typer

from voicegateway.cli._app import app, console
from voicegateway.utils.cli._shared import _load_gateway
from voicegateway.utils.cli.serve import _resolve_bind


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
