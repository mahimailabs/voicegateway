"""``voicegw serve`` command."""

from __future__ import annotations

import typer

from voicegateway.cli._app import app
from voicegateway.cli.base_cli import BaseCli
from voicegateway.core.auth import describe_auth, load_api_keys
from voicegateway.server import build_app
from voicegateway.utils.cli.serve import _resolve_bind

_cli = BaseCli()


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
    except ImportError:
        _cli.fail(
            "Dashboard dependencies not installed. "
            "Run: pip install 'voicegateway[dashboard]'"
        )

    # Publish the served config path so request-time resolvers that read the
    # raw YAML directly (e.g. the LiveKit diagnostics creds resolver) find the
    # same file the daemon serves, not just ``./voicegw.yaml`` relative to the
    # daemon's working directory.
    if config:
        import os

        os.environ.setdefault("VOICEGW_CONFIG", config)

    gw = _cli.require_gateway(config)
    host, port = _resolve_bind(getattr(gw.config, "serve", None), host, port)

    api_app = build_app(gw)
    _cli.success(f"VoiceGateway API starting at http://{host}:{port}")
    _cli.info(describe_auth(load_api_keys(gw.config.auth)))
    uvicorn.run(api_app, host=host, port=port)
