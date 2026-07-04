"""`voicegw livekit ...`: LiveKit deployment diagnostics.

A thin Typer surface over voicegateway.livekit_diag. Commands added in later
tasks: agents, latency, sfu, check. Creds resolve via shared options.
"""

from __future__ import annotations

import asyncio
import json as _json

import typer

from voicegateway.cli._app import app
from voicegateway.cli.base_cli import BaseCli
from voicegateway.livekit_diag.admin import LiveKitAdmin
from voicegateway.livekit_diag.config import CredsError, resolve_creds
from voicegateway.livekit_diag.report import agents_json, render_agents

_cli = BaseCli()

livekit_app = typer.Typer(help="Diagnose a LiveKit deployment (agents, latency, SFU).")


def _creds(url: str | None, api_key: str | None, api_secret: str | None, config: str | None):
    try:
        return resolve_creds(url, api_key, api_secret, config)
    except CredsError as exc:
        _cli.error(str(exc))
        raise typer.Exit(1) from None


@livekit_app.command("agents")
def agents_cmd(
    url: str = typer.Option(None, "--url"),
    api_key: str = typer.Option(None, "--api-key"),
    api_secret: str = typer.Option(None, "--api-secret"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List agents currently in rooms on the LiveKit server."""
    creds = _creds(url, api_key, api_secret, config)

    async def _run():
        admin = LiveKitAdmin(creds)
        try:
            return await admin.list_agents()
        finally:
            await admin.aclose()

    try:
        rows = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 - diagnostics never crash raw
        _cli.error(f"could not reach LiveKit server: {exc}")
        raise typer.Exit(1) from None
    if as_json:
        _cli.console.print_json(_json.dumps(agents_json(rows)))
    else:
        _cli.console.print(render_agents(rows))


app.add_typer(livekit_app, name="livekit")
