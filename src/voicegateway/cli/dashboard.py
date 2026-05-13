"""``voicegw dashboard`` command."""

from __future__ import annotations

import typer

from voicegateway.cli._app import app, console
from voicegateway.cli._helpers import _load_gateway


@app.command(name="dashboard")
def dashboard_cmd(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
    host: str = typer.Option("0.0.0.0", "--host", help="Dashboard host"),
    port: int = typer.Option(9090, "--port", help="Dashboard port"),
) -> None:
    """Start the web dashboard."""
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

    console.print(f"[green]VoiceGateway dashboard at http://{host}:{port}[/green]")
    console.print(f"[cyan]{describe_auth(load_api_keys(gw.config.auth))}[/cyan]")

    import dashboard.api.main as dashboard_app

    dashboard_app.configure(gw)
    uvicorn.run(dashboard_app.app, host=host, port=port)
