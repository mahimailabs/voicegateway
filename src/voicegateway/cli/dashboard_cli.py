"""``voicegw dashboard`` command."""

from __future__ import annotations

import typer

from voicegateway.cli._app import app
from voicegateway.cli.base_cli import BaseCli
from voicegateway.core.auth import describe_auth, load_api_keys

_cli = BaseCli()


@app.command(name="dashboard")
def dashboard_cmd(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
    host: str = typer.Option("0.0.0.0", "--host", help="Dashboard host"),
    port: int = typer.Option(9090, "--port", help="Dashboard port"),
) -> None:
    """Start the web dashboard."""
    try:
        import uvicorn
    except ImportError:
        _cli.fail(
            "Dashboard dependencies not installed. "
            "Run: pip install 'voicegateway[dashboard]'"
        )

    gw = _cli.require_gateway(config)

    _cli.success(f"VoiceGateway dashboard at http://{host}:{port}")
    _cli.info(describe_auth(load_api_keys(gw.config.auth)))

    import dashboard.api.main as dashboard_app

    dashboard_app.configure(gw)
    uvicorn.run(dashboard_app.app, host=host, port=port)
