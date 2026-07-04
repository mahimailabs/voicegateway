"""`voicegw livekit ...`: LiveKit deployment diagnostics.

A thin Typer surface over voicegateway.livekit_diag. Commands added in later
tasks: agents, latency, sfu, check. Creds resolve via shared options.
"""

from __future__ import annotations

import typer

from voicegateway.cli._app import app
from voicegateway.cli.base_cli import BaseCli
from voicegateway.livekit_diag.config import CredsError, resolve_creds

_cli = BaseCli()

livekit_app = typer.Typer(help="Diagnose a LiveKit deployment (agents, latency, SFU).")


def _creds(url: str | None, api_key: str | None, api_secret: str | None, config: str | None):
    try:
        return resolve_creds(url, api_key, api_secret, config)
    except CredsError as exc:
        _cli.error(str(exc))
        raise typer.Exit(1) from None


app.add_typer(livekit_app, name="livekit")
