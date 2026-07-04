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
import pathlib

from voicegateway.livekit_diag.report import agents_json, render_agents, render_latency

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


from voicegateway.livekit_diag.client import SyntheticClient, UtteranceSource
from voicegateway.livekit_diag.latency import ComponentReader, ProbeRunner, summarize


def _utterance_path() -> str:
    import voicegateway.livekit_diag as pkg
    return str(pathlib.Path(pkg.__file__).parent / "assets" / "probe.wav")


@livekit_app.command("latency")
def latency_cmd(
    agent: list[str] = typer.Option(None, "--agent"),
    trials: int = typer.Option(3, "--trials"),
    warmup: bool = typer.Option(True, "--warmup/--no-warmup"),
    room_name: str = typer.Option(None, "--room-name"),
    metadata: str = typer.Option("", "--metadata"),
    target_ms: float = typer.Option(1500, "--target-ms"),
    url: str = typer.Option(None, "--url"),
    api_key: str = typer.Option(None, "--api-key"),
    api_secret: str = typer.Option(None, "--api-secret"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    """Actively probe per-agent latency (end-to-end + breakdown)."""
    creds = _creds(url, api_key, api_secret, config)
    _cli.warn("Each probe places a real call and incurs real provider cost.")

    async def _run():
        admin = LiveKitAdmin(creds)
        admin.url = creds.url  # let ProbeRunner build client urls
        try:
            targets = agent or [r.agent_name for r in await admin.list_agents()]
            runner = ProbeRunner(admin, lambda u, t: SyntheticClient(creds.url, t),
                                  UtteranceSource(_utterance_path()), ComponentReader())
            out = []
            for name in targets:
                out.append(await runner.probe(name, trials, warmup, room_name, metadata))
            return out
        finally:
            await admin.aclose()

    try:
        results = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        _cli.error(f"latency probe failed: {exc}")
        raise typer.Exit(1) from None
    _cli.console.print(render_latency(results, target_ms, summarize))


app.add_typer(livekit_app, name="livekit")
