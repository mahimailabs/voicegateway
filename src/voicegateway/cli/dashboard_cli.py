"""``voicegw dashboard`` command: open the daemon's dashboard URL.

The daemon (``voicegw serve`` or the OS-installed background daemon)
already serves the React SPA at ``/`` and all ``/api/*`` endpoints on
the same port. This command does not start a second process; it just
opens the browser at the daemon's URL.
"""

from __future__ import annotations

import webbrowser

import typer

from voicegateway.cli._app import app
from voicegateway.cli.base_cli import BaseCli
from voicegateway.utils.cli.serve import _resolve_bind

_cli = BaseCli()


@app.command(name="dashboard")
def dashboard_cmd(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
    no_open: bool = typer.Option(
        False,
        "--no-open",
        help="Print the dashboard URL without launching a browser.",
    ),
) -> None:
    """Open the VoiceGateway dashboard in your browser.

    The daemon serves the dashboard at the same address as the HTTP
    API. This command resolves that address from your config and (by
    default) opens your browser at it.
    """
    gw = _cli.require_gateway(config)
    host, port = _resolve_bind(getattr(gw.config, "serve", None), None, None)
    if host in ("0.0.0.0", "::"):
        host = "localhost"
    url = f"http://{host}:{port}"
    _cli.success(f"Dashboard: {url}")
    if no_open:
        return
    if not webbrowser.open(url):
        _cli.warn("Could not auto-launch a browser. Visit the URL above manually.")
