"""`voicegw livekit ...`: LiveKit deployment diagnostics.

A thin Typer surface over voicegateway.livekit_diag. Commands added in later
tasks: agents, latency, sfu, check. Creds resolve via shared options.

``report`` is the odd one out: it probes nothing. It exports a run the dashboard
already recorded, through the same renderer the dashboard's own download button
uses (:mod:`voicegateway.livekit_diag.run_report`), so CI can collect the
artifact on a host that never runs the dashboard.
"""

from __future__ import annotations

import asyncio
import json as _json
import os
import pathlib
from typing import Any

import typer

from voicegateway.cli._app import app
from voicegateway.cli.base_cli import BaseCli
from voicegateway.livekit_diag import gates, run_report
from voicegateway.livekit_diag.admin import LiveKitAdmin
from voicegateway.livekit_diag.client import SyntheticClient, UtteranceSource
from voicegateway.livekit_diag.config import CredsError, LiveKitCreds, resolve_creds
from voicegateway.livekit_diag.distributed import (
    Coordinator,
    run_prober,
    serve_coordinator,
)
from voicegateway.livekit_diag.latency import ComponentReader, ProbeRunner, summarize
from voicegateway.livekit_diag.report import (
    agents_json,
    check_json,
    render_agents,
    render_check,
    render_distributed_sfu,
    render_latency,
    render_sfu,
)
from voicegateway.livekit_diag.resources import ResourceMonitor
from voicegateway.livekit_diag.roster import fetch_roster
from voicegateway.livekit_diag.sfu import SfuProbe, find_knee

_cli = BaseCli()

livekit_app = typer.Typer(help="Diagnose a LiveKit deployment (agents, latency, SFU).")


def _creds(
    url: str | None, api_key: str | None, api_secret: str | None, config: str | None
) -> LiveKitCreds:
    try:
        return resolve_creds(url, api_key, api_secret, config)
    except CredsError as exc:
        _cli.error(str(exc))
        raise typer.Exit(1) from None


def _utterance_path() -> str:
    import voicegateway.livekit_diag as pkg

    return str(pathlib.Path(pkg.__file__).parent / "assets" / "probe.wav")


def _component_reader() -> ComponentReader:
    """A reader that reads the STT/LLM/TTS split back from the local VG store.

    Read-back is co-located: the instrumented agent's ``attach`` wrote the split
    to the local SQLite DB, and the probe correlates on the room name. In
    collector mode the rows went to the collector, not this host, so return a
    storeless reader (the split simply won't render). We also skip it when no DB
    file exists yet, to avoid alembic-bootstrapping a DB just to read nothing.
    """
    if os.environ.get("VOICEGW_COLLECTOR_URL"):
        return ComponentReader()
    db_path = os.environ.get("VOICEGW_DB_PATH") or os.path.expanduser(
        "~/.config/voicegateway/voicegw.db"
    )
    if not os.path.exists(db_path):
        return ComponentReader()
    from voicegateway.services.storage_service import StorageService

    # Poll ~2s: the agent flushes its rows from another process around when the
    # probe finishes, so the first read can race ahead of the write.
    return ComponentReader(StorageService(db_path), poll_attempts=6, poll_delay=0.4)


@livekit_app.command("agents")
def agents_cmd(
    url: str = typer.Option(None, "--url"),
    api_key: str = typer.Option(None, "--api-key"),
    api_secret: str = typer.Option(None, "--api-secret"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List agents currently in rooms on the LiveKit server.

    When ``VOICEGW_COLLECTOR_URL`` (and ``VOICEGW_API_KEY``) are set, also fetch
    the heartbeat roster so idle/registered workers show alongside the in-room
    agents. Without the collector, only the in-room view (the LiveKit server API)
    is shown, plus a note on how to enable the roster.
    """
    creds = _creds(url, api_key, api_secret, config)
    collector = os.environ.get("VOICEGW_COLLECTOR_URL")

    async def _run():
        admin = LiveKitAdmin(creds)
        try:
            rows = await admin.list_agents()
        finally:
            await admin.aclose()
        roster = None
        if collector:
            roster = await fetch_roster(collector, os.environ.get("VOICEGW_API_KEY"))
        return rows, roster

    try:
        rows, roster = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 - diagnostics never crash raw
        _cli.error(f"could not reach LiveKit server: {exc}")
        raise typer.Exit(1) from None
    if as_json:
        _cli.console.print_json(
            _json.dumps({"in_room": agents_json(rows), "roster": roster or []})
        )
    else:
        _cli.console.print(render_agents(rows, roster))


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
            runner = ProbeRunner(
                admin,
                lambda u, t: SyntheticClient(creds.url, t),
                UtteranceSource(_utterance_path()),
                _component_reader(),
            )
            out = []
            for name in targets:
                out.append(
                    await runner.probe(name, trials, warmup, room_name, metadata)
                )
            return out
        finally:
            await admin.aclose()

    try:
        results = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        _cli.error(f"latency probe failed: {exc}")
        raise typer.Exit(1) from None
    _cli.console.print(render_latency(results, target_ms, summarize))


@livekit_app.command("sfu")
def sfu_cmd(
    load: bool = typer.Option(False, "--load"),
    ramp: str = typer.Option("2,10,25,50", "--ramp"),
    duration: float = typer.Option(20.0, "--duration"),
    room: str = typer.Option("vg-sfu-probe", "--room"),
    target_rtt_ms: float = typer.Option(50.0, "--target-rtt-ms"),
    max_loss: float = typer.Option(1.0, "--max-loss"),
    coordinator: bool = typer.Option(False, "--coordinator"),
    expect: int = typer.Option(0, "--expect"),
    coordinator_port: int = typer.Option(8787, "--coordinator-port"),
    report_to: str = typer.Option(None, "--report-to"),
    vantage: str = typer.Option(None, "--vantage"),
    url: str = typer.Option(None, "--url"),
    api_key: str = typer.Option(None, "--api-key"),
    api_secret: str = typer.Option(None, "--api-secret"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    """Measure SFU connection quality (single host, or distributed).

    Single host: ``--load`` ramps synthetic clients from this machine.
    Distributed: run one ``--coordinator --expect N``, then N probers with
    ``--report-to <coordinator-url> --vantage <label>``; every vantage ramps the
    same room at once and the coordinator prints the combined capacity.
    """
    if report_to:
        _run_sfu_prober(report_to, vantage, ramp, url, api_key, api_secret, config)
        return
    if coordinator:
        _run_sfu_coordinator(
            expect,
            coordinator_port,
            room,
            ramp,
            duration,
            target_rtt_ms,
            max_loss,
            url,
            api_key,
            api_secret,
            config,
        )
        return

    creds = _creds(url, api_key, api_secret, config)

    async def _run():
        admin = LiveKitAdmin(creds)
        admin.url = creds.url
        probe = SfuProbe(admin, lambda u, t: SyntheticClient(u, t), ResourceMonitor())
        try:
            base = await probe.baseline(room)
            steps, resource = ([], None)
            if load:
                counts = [int(x) for x in ramp.split(",") if x.strip()]
                steps, resource = await probe.ramp(
                    room, counts, duration, target_rtt_ms, max_loss
                )
            knee = find_knee(steps, target_rtt_ms, max_loss) if steps else None
            return base, steps, resource, knee
        finally:
            await admin.aclose()

    try:
        base, steps, resource, knee = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        _cli.error(f"sfu probe failed: {exc}")
        raise typer.Exit(1) from None
    _cli.console.print(render_sfu("co-located", base, steps, resource, knee))


def _run_sfu_prober(report_to, vantage, ramp, url, api_key, api_secret, config):
    """Prober mode: register, wait for the barrier, ramp the shared room, report."""
    creds = _creds(url, api_key, api_secret, config)
    label = vantage or os.environ.get("VOICEGW_REGION") or "vantage"
    _cli.warn(f"Reporting to coordinator {report_to} as vantage '{label}'.")

    async def _run():
        admin = LiveKitAdmin(creds)
        admin.url = creds.url
        probe = SfuProbe(admin, lambda u, t: SyntheticClient(u, t), ResourceMonitor())
        try:
            return await run_prober(report_to, probe, vantage=label)
        finally:
            await admin.aclose()

    try:
        payload = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        _cli.error(f"prober failed: {exc}")
        raise typer.Exit(1) from None
    _cli.console.print(f"vantage '{label}' reported {len(payload['steps'])} tiers.")


def _run_sfu_coordinator(
    expect,
    port,
    room,
    ramp,
    duration,
    target_rtt_ms,
    max_loss,
    url,
    api_key,
    api_secret,
    config,
):
    """Coordinator mode: serve the barrier, aggregate reports, clean up rooms."""
    if expect < 1:
        _cli.error("--coordinator needs --expect N (number of probers, >= 1).")
        raise typer.Exit(1)
    creds = _creds(url, api_key, api_secret, config)
    counts = [int(x) for x in ramp.split(",") if x.strip()]
    coord = Coordinator(
        expect=expect,
        room=room,
        ramp=counts,
        duration=duration,
        target_rtt_ms=target_rtt_ms,
        max_loss=max_loss,
    )
    _cli.warn(f"Waiting for {expect} prober(s) on port {port}; ramp {counts}.")

    async def _run():
        result = await serve_coordinator(coord, port=port)
        admin = LiveKitAdmin(creds)
        admin.url = creds.url
        try:
            for tier_room in coord.rooms:
                await admin.delete_room(tier_room)  # best-effort shared cleanup
        finally:
            await admin.aclose()
        return result

    try:
        result = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        _cli.error(f"coordinator failed: {exc}")
        raise typer.Exit(1) from None
    reported = len(result.get("vantages", []))
    if reported < expect:
        _cli.warn(
            f"only {reported}/{expect} prober(s) reported before the timeout; "
            "aggregating what arrived."
        )
    _cli.console.print(render_distributed_sfu(result))


@livekit_app.command("check")
def check_cmd(
    as_json: bool = typer.Option(False, "--json"),
    strict: bool = typer.Option(
        False,
        "--strict",
        help=(
            "Gate the slowest measured turn instead of the average. With fewer "
            "than 10 samples the metric is named max_of_N, never p95."
        ),
    ),
    target_ms: float = typer.Option(1500, "--target-ms"),
    url: str = typer.Option(None, "--url"),
    api_key: str = typer.Option(None, "--api-key"),
    api_secret: str = typer.Option(None, "--api-secret"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    """Run agents + latency + sfu baseline and print one verdict report.

    Exits 0 only on a clean PASS. WARN, FAIL and UNKNOWN all exit 1: a gate that
    could not be evaluated (no agent answered, no baseline came back) has not
    demonstrated a healthy deployment, and reporting 0 for it would make this
    command lie to CI. That is stricter than it used to be -- see the CHANGELOG.
    """
    creds = _creds(url, api_key, api_secret, config)

    async def _run():
        admin = LiveKitAdmin(creds)
        admin.url = creds.url
        try:
            agents = await admin.list_agents()
            runner = ProbeRunner(
                admin,
                lambda u, t: SyntheticClient(creds.url, t),
                UtteranceSource(_utterance_path()),
                _component_reader(),
            )
            lat = [await runner.probe(a.agent_name, 2, True, None, "") for a in agents]
            probe = SfuProbe(
                admin, lambda u, t: SyntheticClient(creds.url, t), ResourceMonitor()
            )
            base = await probe.baseline("vg-check")
            return agents, lat, base
        finally:
            await admin.aclose()

    try:
        agents, lat, base = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        _cli.error(f"check failed: {exc}")
        raise typer.Exit(1) from None
    # Computed once. Rendering used to recompute the verdict independently of
    # the JSON branch, which is the same trap this node exists to close.
    js = check_json(
        agents, lat, base, [], None, None, summarize, target_ms, strict=strict
    )
    if as_json:
        _cli.console.print_json(_json.dumps(js))
    else:
        _cli.console.print(
            render_check(
                agents, lat, base, [], None, None, summarize, target_ms, strict=strict
            )
        )
    raise typer.Exit(gates.exit_code(js["verdict"]))


def _report_url(
    url: str | None, api_key: str | None, api_secret: str | None, config: str | None
) -> str | None:
    """The LiveKit server to name in the report, or None.

    Deliberately NOT :func:`_creds`: every other subcommand needs credentials to
    do its work and exits 1 without them, but ``report`` only reads a run that
    already happened. Missing credentials cost it one labelled line ("not
    recorded"), not the artifact. Same resolution the dashboard endpoint does, so
    the two agree on the same host.
    """
    try:
        return resolve_creds(url, api_key, api_secret, config).url
    except CredsError:
        return None


async def _fetch_run(storage: Any, run_id: str | None) -> Any:
    """One stored diagnostics run as a row, newest when no id was given.

    One coroutine for both reads on purpose: a second ``asyncio.run`` would hand
    the same storage engine to a second event loop.
    """
    if run_id:
        return await storage.get_diagnostics_run(run_id)
    rows = await storage.list_diagnostics_runs(limit=1)
    return rows[0] if rows else None


@livekit_app.command("report")
def report_cmd(
    run: str = typer.Option(
        None, "--run", help="Run id to export (default: the most recent run recorded)"
    ),
    out: str = typer.Option(
        None,
        "--out",
        help=(
            "Where to write it (default: ./voicegateway-diagnostics-<run>.html; "
            "with --json, the payload goes to stdout unless --out is given)"
        ),
    ),
    as_json: bool = typer.Option(False, "--json"),
    url: str = typer.Option(None, "--url"),
    api_key: str = typer.Option(None, "--api-key"),
    api_secret: str = typer.Option(None, "--api-secret"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    """Export a recorded diagnostics run as one self-contained HTML file.

    Reads the run out of this host's store (the dashboard writes one row per
    run) and renders it through
    :mod:`voicegateway.livekit_diag.run_report` -- the same payload and the same
    document the dashboard's ``/report.html`` serves, byte for byte, with no web
    framework in the path. The file loads nothing over the network, so it reads
    the same offline months later.

    This command judges nothing and probes nothing: no call is placed, no
    provider is billed, and the verdict is the one the run recorded. It exits 0
    whenever it produced the artifact, whatever that verdict says -- gating CI on
    a run's health is ``voicegw livekit check``'s job, and exiting 1 on a FAIL
    here would throw away the report that explains it.
    """
    gw = _cli.require_gateway(config)
    storage = _cli.require_storage(gw)
    try:
        row = _cli.async_run(_fetch_run(storage, run))
    except Exception as exc:  # noqa: BLE001 - diagnostics never crash raw
        _cli.error(f"could not read the diagnostics run store: {exc}")
        raise typer.Exit(1) from None
    if row is None:
        _cli.fail(
            f"no diagnostics run {run!r} is recorded on this host."
            if run
            else "no diagnostics run is recorded on this host yet. Start one from "
            "the dashboard's Diagnostics page; this command exports what a run "
            "already recorded, it does not run one."
        )

    record = run_report.run_from_row(row)
    payload = run_report.build_payload(
        record, livekit_url=_report_url(url, api_key, api_secret, config)
    )
    if as_json and not out:
        _cli.console.print_json(_json.dumps(payload))
        return

    if as_json:
        document = _json.dumps(payload, indent=2) + "\n"
    else:
        document = run_report.render_html(payload)
    default_name = run_report.report_filename(record.run_id)
    path = pathlib.Path(out) if out else pathlib.Path(default_name)
    try:
        path.write_text(document, encoding="utf-8")
    except OSError as exc:
        _cli.fail(f"could not write {path}: {exc}")
    _cli.success(f"Wrote {path} ({len(document.encode('utf-8')):,} bytes).")
    # The verdict is reproduced, never recomputed: it is what the run recorded.
    _cli.dim(
        f"run {record.run_id} · status {record.status} · verdict "
        f"{record.verdict or 'none recorded'}"
    )


app.add_typer(livekit_app, name="livekit")
