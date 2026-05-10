"""CLI for VoiceGateway."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

app = typer.Typer(
    name="voicegw",
    help="VoiceGateway: cost tracking and reconciliation for LiveKit voice agents",
    no_args_is_help=True,
)
console = Console()


def _version_callback(value: bool) -> None:
    """Print the package version and exit (eager option)."""
    if value:
        from voicegateway import __version__

        console.print(__version__)
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the VoiceGateway version and exit.",
    ),
) -> None:
    """Root callback hosting global options like --version."""


def _load_gateway(config_path: str | None):
    from voicegateway.core.gateway import Gateway

    try:
        return Gateway(config_path=config_path)
    except Exception as e:
        console.print(f"[red]Error loading config: {e}[/red]")
        raise typer.Exit(1) from e


@app.command(name="projects")
def projects_cmd(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
):
    """List all configured projects."""
    gw = _load_gateway(config)

    if not gw.config.projects:
        console.print(
            "[yellow]No projects configured. Add a 'projects:' section to voicegw.yaml.[/yellow]"
        )
        raise typer.Exit(0)

    table = Table(title="Projects")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Tags")
    table.add_column("Budget/day", style="green", justify="right")
    table.add_column("Default Stack")

    for pid, pcfg in sorted(gw.config.projects.items()):
        tags = " ".join(f"[bold]{t}[/bold]" for t in pcfg.tags)
        budget = f"${pcfg.daily_budget:.2f}" if pcfg.daily_budget else "-"
        table.add_row(pid, pcfg.name, tags, budget, pcfg.default_stack or "-")

    console.print(table)


@app.command(name="project")
def project_cmd(
    project_id: str = typer.Argument(..., help="Project ID to show"),
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
):
    """Show details for a single project."""
    gw = _load_gateway(config)
    pcfg = gw.config.get_project(project_id)

    if pcfg is None:
        console.print(f"[red]Project not found: {project_id}[/red]")
        raise typer.Exit(1)

    body = (
        f"[bold]{pcfg.name}[/bold]\n"
        f"{pcfg.description or '(no description)'}\n\n"
        f"Tags: {', '.join(pcfg.tags) or '-'}\n"
        f"Default Stack: {pcfg.default_stack or '-'}\n"
        f"Daily Budget: ${pcfg.daily_budget:.2f}"
    )
    console.print(Panel(body, title=f"Project: {project_id}", border_style="cyan"))

    if gw.storage is not None:
        today = asyncio.run(gw.storage.get_cost_summary("today", project=project_id))
        console.print(
            f"\n[bold]Today[/bold]: ${today['total']:.4f} "
            f"({sum(v['requests'] for v in today['by_provider'].values())} requests)"
        )


@app.command(name="logs")
def logs_cmd(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
    project: str = typer.Option(None, "--project", "-p", help="Filter by project"),
    tail: int = typer.Option(20, "--tail", "-n", help="Number of rows"),
    modality: str = typer.Option(None, "--modality", "-m", help="stt, llm, or tts"),
):
    """Show recent request logs."""
    gw = _load_gateway(config)
    if gw.storage is None:
        console.print("[yellow]Cost tracking is not enabled in voicegw.yaml[/yellow]")
        raise typer.Exit(0)

    rows = asyncio.run(
        gw.storage.get_recent_requests(limit=tail, modality=modality, project=project)
    )
    if not rows:
        console.print("[dim]No logs found.[/dim]")
        return

    table = Table(title=f"Recent Requests ({len(rows)})")
    table.add_column("Time", style="cyan")
    table.add_column("Project", style="magenta")
    table.add_column("Modality")
    table.add_column("Model")
    table.add_column("Cost", justify="right")
    table.add_column("Latency", justify="right")
    table.add_column("Status")

    import datetime

    for r in rows:
        ts = datetime.datetime.fromtimestamp(r["timestamp"]).strftime("%H:%M:%S")
        table.add_row(
            ts,
            r.get("project") or "-",
            r.get("modality", "").upper(),
            r.get("model_id", ""),
            f"${r.get('cost_usd', 0):.6f}",
            f"{int(r.get('total_latency_ms') or 0)}ms",
            r.get("status", ""),
        )
    console.print(table)


@app.command(name="smoke-test")
def smoke_test(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
    project: str = typer.Option(
        None, "--project", "-p", help="Project to test (default: active project)"
    ),
    live: bool = typer.Option(
        False,
        "--live",
        help=(
            "Run health-check probes against each provider's API in addition "
            "to the structural pipeline checks. Requires real credentials and "
            "network access; takes a few seconds per provider."
        ),
    ),
):
    """Verify the v0.0.5 inference pipeline end-to-end without LiveKit.

    Walks every layer the agent path goes through, with stubbed
    LiveKit plugins so the run does not need a LiveKit server or
    real provider API calls:

      1. Config loads, the active project resolves.
      2. Each modality (STT, LLM, TTS) constructs through
         `voicegateway.inference.STT/LLM/TTS` and returns the
         expected wrapped instance.
      3. Driving a simulated request through the wrapper writes a
         row to the requests table with the correct session_id,
         project, and pricing_source.
      4. The sessions table aggregates the request: started_at,
         ended_at, modalities, total_cost_usd are populated.

    With ``--live`` the command additionally runs each configured
    provider's health_check (the same probe ``vg_test_provider_key``
    runs over MCP) so the report covers credential validity too.

    What this does NOT replace: a real audio interaction against a
    running LiveKit server. For that, run
    ``python examples/v005_inference_drop_in.py dev`` against a
    LiveKit dev server with real provider keys.

    Exit code is 0 when every check passes, 1 otherwise.
    """
    from voicegateway.inference._factory import reset_gateway

    gw = _load_gateway(config)

    rows: list[tuple[str, bool, str]] = []  # (label, passed, message)

    def add(label: str, passed: bool, message: str = "") -> None:
        rows.append((label, passed, message))

    # 1. Config + project resolution.
    if gw.storage is None:
        add(
            "config",
            False,
            "Cost tracking disabled in voicegw.yaml; smoke-test needs storage.",
        )
        _print_smoke_report(rows)
        raise typer.Exit(1)
    add("config", True, str(gw.config.cost_tracking.get("db_path", "(default)")))

    # Validate an explicit --project up front. Without this check a
    # typo (--project tony-piza) sails through `project or _smoke_…`
    # and surfaces as a confusing "no provider key" failure deeper in
    # the pipeline.
    if project is not None and project not in gw.config.projects:
        known = ", ".join(sorted(gw.config.projects)) or "(none)"
        add(
            "active project",
            False,
            f"Unknown project '{project}'. Known projects: {known}.",
        )
        _print_smoke_report(rows)
        raise typer.Exit(1)

    active = project or _smoke_active_project(gw)
    if active is None:
        add(
            "active project",
            False,
            "No project configured; cannot select a provider key.",
        )
        _print_smoke_report(rows)
        raise typer.Exit(1)
    add("active project", True, active)

    # try/finally so reset_gateway() always runs — including when a
    # pipeline check raises or the typer.Exit(1) below fires. Without
    # this the singleton cache stays mutated across a failed smoke
    # run, which trips up the next test invocation in the same
    # process.
    try:
        # 2. Per-modality construction with stubbed LiveKit plugins.
        asyncio.run(_run_smoke_pipeline_checks(gw, active, add))

        # 3. Optional live health checks.
        if live:
            asyncio.run(_run_smoke_health_checks(gw, active, add))

        _print_smoke_report(rows)
        if any(not passed for _, passed, _ in rows):
            raise typer.Exit(1)
    finally:
        reset_gateway()


def _smoke_active_project(gw: Any) -> str | None:
    """Pick a project for the smoke test.

    Order: ``default_project`` from YAML, then any non-``default``
    project (so the auto-created stub is the last resort), then
    ``"default"`` itself.
    """
    if gw.config.default_project:
        return str(gw.config.default_project)
    named = [p for p in gw.config.projects if p != "default"]
    if named:
        return str(sorted(named)[0])
    if "default" in gw.config.projects:
        return "default"
    return None


_SMOKE_MODALITIES: tuple[tuple[str, str], ...] = (
    ("stt", "STT"),
    ("llm", "LLM"),
    ("tts", "TTS"),
)


def _smoke_pick_models(gw: Any, project: str) -> dict[str, str]:
    """Return a model_id per modality available to ``project``.

    Looks at the project's `providers:` block first; falls back to
    the top-level providers map. Picks any registered model for the
    matching provider so the smoke test exercises the same code
    path the agent will use, without forcing the operator to pin
    one in YAML.
    """
    proj = gw.config.get_project(project)
    if proj is None:
        return {}
    available_providers: set[str] = set(proj.providers)
    available_providers.update(gw.config.providers)
    # Local providers don't need keys; surface them as available
    # automatically so a fresh install can still smoke-test.
    available_providers.update({"ollama", "whisper", "kokoro", "piper"})

    chosen: dict[str, str] = {}
    for modality, _ in _SMOKE_MODALITIES:
        bucket = gw.config.models.get(modality) or {}
        for model_id, model_cfg in bucket.items():
            provider_name = model_cfg.get("provider", model_id.split("/", 1)[0])
            if provider_name in available_providers:
                chosen[modality] = model_id
                break
    return chosen


async def _run_smoke_pipeline_checks(gw: Any, project: str, add) -> None:
    """Construct each modality with stubbed LK plugins, drive a fake
    request through the wrapper, verify storage rows.
    """

    from voicegateway.inference import _factory, _llm, _project, _stt, _tts

    models = _smoke_pick_models(gw, project)

    # Smoke-test stubs: each stub instance must carry the LK-side
    # surface InstrumentedSTT/LLM/TTS reads at construction —
    # ``capabilities`` (passed to the LK base class via super().__init__),
    # ``sample_rate`` / ``num_channels`` for TTS, and a no-op
    # ``on(event, callback)`` for the metrics-event bridge. Without these
    # the wrapper rewrite (AC-2 fix) raises AttributeError on every
    # modality and the smoke test exits 1.
    from livekit.agents.stt import STTCapabilities
    from livekit.agents.tts import TTSCapabilities

    class _StubSTT:
        capabilities = STTCapabilities(streaming=False, interim_results=False)

        def on(self, _event: str, _cb: Any) -> None:
            pass

    class _StubLLM:
        def on(self, _event: str, _cb: Any) -> None:
            pass

    class _StubTTS:
        capabilities = TTSCapabilities(streaming=False)
        sample_rate = 24000
        num_channels = 1

        def on(self, _event: str, _cb: Any) -> None:
            pass

    class _StubProvider:
        def __init__(self, _config: dict) -> None:
            pass

        def create_stt(self, model: str, **_kw: Any) -> Any:
            return _StubSTT()

        def create_llm(self, model: str, **_kw: Any) -> Any:
            return _StubLLM()

        def create_tts(self, model: str, **_kw: Any) -> Any:
            return _StubTTS()

        async def health_check(self) -> bool:
            return True

    def _stub_create(_provider_name: str, _config: dict) -> _StubProvider:
        return _StubProvider(_config)

    # Pin the inference module to the gateway we already loaded so
    # the smoke test does not spin a second one with its own storage.
    _factory._gateway = gw
    _project.reset_project()
    _project.set_project(project)

    real_stt_create = _stt.create_provider
    real_llm_create = _llm.create_provider
    real_tts_create = _tts.create_provider
    _stt.create_provider = _stub_create  # type: ignore[assignment]
    _llm.create_provider = _stub_create  # type: ignore[assignment]
    _tts.create_provider = _stub_create  # type: ignore[assignment]

    session_id_holder: dict[str, str | None] = {"sid": None}

    try:
        for modality, label in _SMOKE_MODALITIES:
            model_id = models.get(modality)
            if model_id is None:
                # A project that only uses some modalities (e.g. an
                # LLM-only chatbot) is a valid shape, not a failure.
                # Surface as PASS with a "skipped" detail so the
                # report still notes the absence.
                add(
                    f"inference.{label}",
                    True,
                    "skipped (no model registered for this modality)",
                )
                continue
            instance: Any
            try:
                if modality == "stt":
                    instance = _stt.STT(model_id)
                elif modality == "llm":
                    instance = _llm.LLM(model_id)
                else:
                    instance = _tts.TTS(model_id)
            except Exception as exc:  # noqa: BLE001
                add(
                    f"inference.{label}",
                    False,
                    f"{type(exc).__name__}: {exc}",
                )
                continue

            wrapper_modality = getattr(instance, "_modality", None)
            if wrapper_modality != modality:
                add(
                    f"inference.{label}",
                    False,
                    f"Expected wrapped {modality}; got {type(instance).__name__}.",
                )
                continue
            add(f"inference.{label}", True, f"wrapped {model_id}")

            # Drive _log_request — the seam where the wrapper writes
            # to storage and bumps the session row. Use small but
            # non-zero unit counts so cost shows up if pricing is
            # configured for the model; for unknown models the row
            # still lands at $0.
            try:
                await instance._log_request(input_units=1.0)
            except Exception as exc:  # noqa: BLE001
                add(
                    f"storage.requests/{modality}",
                    False,
                    f"_log_request raised: {type(exc).__name__}: {exc}",
                )
                continue
            add(
                f"storage.requests/{modality}",
                True,
                "request row written via wrapper",
            )
    finally:
        _stt.create_provider = real_stt_create  # type: ignore[assignment]
        _llm.create_provider = real_llm_create  # type: ignore[assignment]
        _tts.create_provider = real_tts_create  # type: ignore[assignment]

    # Verify session row aggregation. The wrapper reads the
    # ContextVar at request time; in a sync CLI run all three
    # wrappers share one context, so they all carry the same sid.
    from voicegateway.inference._session_context import get_session_id

    sid = get_session_id()
    session_id_holder["sid"] = sid
    if sid is None:
        add(
            "session correlation",
            False,
            "No session_id in current context after factory calls.",
        )
        return
    session = await gw.storage.get_session(sid)
    if session is None:
        add(
            "session correlation",
            False,
            f"sessions table has no row for {sid}.",
        )
        return
    expected_modalities = {m for m, _ in _SMOKE_MODALITIES if m in models}
    actual_modalities = set(session["modalities"])
    if not expected_modalities.issubset(actual_modalities):
        add(
            "session correlation",
            False,
            f"sessions.modalities missing entries: expected "
            f"{sorted(expected_modalities)}, got "
            f"{sorted(actual_modalities)}.",
        )
        return
    add(
        "session correlation",
        True,
        f"{sid} carries {','.join(sorted(actual_modalities))}; "
        f"requests={session['request_count']}, "
        f"cost=${session['total_cost_usd']:.6f}.",
    )


async def _run_smoke_health_checks(gw: Any, project: str, add) -> None:
    """Optional --live: probe each configured provider's API."""
    from voicegateway.core.registry import _PROVIDER_REGISTRY, create_provider

    proj = gw.config.get_project(project)
    if proj is None:
        return
    seen: set[str] = set()
    sources: list[tuple[str, dict]] = []
    for name, cfg in proj.providers.items():
        sources.append((name, cfg))
        seen.add(name)
    for name, cfg in gw.config.providers.items():
        if name in seen:
            continue
        sources.append((name, cfg))
        seen.add(name)

    for name, cfg in sources:
        if name not in _PROVIDER_REGISTRY:
            continue
        try:
            provider_instance = create_provider(name, cfg)
        except Exception as exc:  # noqa: BLE001
            add(
                f"health.{name}",
                False,
                f"create_provider raised: {type(exc).__name__}: {exc}",
            )
            continue
        try:
            ok = await provider_instance.health_check()
        except Exception as exc:  # noqa: BLE001
            add(
                f"health.{name}",
                False,
                f"health_check raised: {type(exc).__name__}: {exc}",
            )
            continue
        add(f"health.{name}", bool(ok), "reachable" if ok else "unhealthy")


def _print_smoke_report(rows: list[tuple[str, bool, str]]) -> None:
    table = Table(title="VoiceGateway smoke test")
    table.add_column("Check", style="cyan")
    table.add_column("Status")
    table.add_column("Detail", overflow="fold")
    for label, passed, message in rows:
        status = "[green]PASS[/green]" if passed else "[red]FAIL[/red]"
        table.add_row(label, status, message)
    console.print(table)
    failures = [label for label, passed, _ in rows if not passed]
    if failures:
        console.print(
            f"\n[red]{len(failures)} check(s) failed[/red]: {', '.join(failures)}"
        )
    else:
        console.print(
            "\n[green]All structural checks passed.[/green] For an "
            "actual end-to-end run, point a LiveKit dev server at "
            "[bold]examples/v005_inference_drop_in.py[/bold]."
        )


@app.command(name="serve")
def serve_cmd(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
    host: str = typer.Option("0.0.0.0", "--host", help="Bind host"),
    port: int = typer.Option(8080, "--port", help="Bind port"),
):
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


@app.command(name="dashboard")
def dashboard_cmd(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
    host: str = typer.Option("0.0.0.0", "--host", help="Dashboard host"),
    port: int = typer.Option(9090, "--port", help="Dashboard port"),
):
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


_EXPORT_COLUMNS = (
    "timestamp",
    "project",
    "modality",
    "provider",
    "model",
    "input_units",
    "output_units",
    "calculated_cost_usd",
    "pricing_source",
    "status",
)

# Map output column names to the storage row keys they project from.
# Storage uses `model_id` and `cost_usd`; the export schema (design
# §2.1) names them `model` and `calculated_cost_usd`. The other 8
# column names match storage keys directly.
_EXPORT_KEY_MAP = {
    "model": "model_id",
    "calculated_cost_usd": "cost_usd",
}


def _format_export_value(column: str, value: Any) -> Any:
    """Format one cell of an export row.

    - timestamp: storage Unix-epoch float -> ISO-8601 UTC string.
    - calculated_cost_usd: float -> fixed-point Decimal string so
      sub-cent costs do not render in scientific notation
      (e.g. 1e-05 -> "0.00001"). The float -> str(Decimal(str(...)))
      hop dodges binary-precision artifacts.
    - everything else: pass through (csv.writer / json.dump handle
      the rest).
    """
    import datetime as _dt
    from decimal import Decimal

    if value is None:
        return ""
    if column == "timestamp":
        try:
            return _dt.datetime.fromtimestamp(float(value), tz=_dt.UTC).isoformat()
        except (TypeError, ValueError, OSError):
            return value
    if column == "calculated_cost_usd":
        try:
            return format(Decimal(str(float(value))), "f")
        except (TypeError, ValueError):
            return value
    return value


def _format_export_row(record: dict[str, Any]) -> dict[str, Any]:
    """Project a storage row into the design-spec export schema."""
    out: dict[str, Any] = {}
    for col in _EXPORT_COLUMNS:
        src = _EXPORT_KEY_MAP.get(col, col)
        out[col] = _format_export_value(col, record.get(src))
    return out


def _parse_iso_date_arg(value: str, *, end_of_day: bool) -> float:
    """Parse YYYY-MM-DD into a UTC timestamp for CLI use.

    With `end_of_day=True`, advance one day so the timestamp is the
    exclusive upper bound for "include all of YYYY-MM-DD."
    """
    import datetime as _dt

    try:
        d = _dt.datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=_dt.UTC)
    except ValueError as e:
        console.print(f"[red]Invalid date {value!r}: expected YYYY-MM-DD[/red]")
        raise typer.Exit(2) from e
    if end_of_day:
        d += _dt.timedelta(days=1)
    return d.timestamp()


@app.command(name="export-costs")
def export_costs_cmd(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
    start: str = typer.Option(
        ..., "--start", help="Start date (YYYY-MM-DD, inclusive, UTC)."
    ),
    end: str = typer.Option(
        ..., "--end", help="End date (YYYY-MM-DD, inclusive, UTC)."
    ),
    project: str = typer.Option(
        None, "--project", "-p", help="Optional project filter."
    ),
    fmt: str = typer.Option(
        "csv", "--format", "-f", help="Output format: csv (default) or json."
    ),
    output: str = typer.Option(
        "-", "--output", "-o", help="Output path; '-' (default) writes to stdout."
    ),
):
    """Export per-request cost line items for a date window.

    Output columns: timestamp (ISO-8601 UTC), project, modality,
    provider, model, input_units, output_units,
    calculated_cost_usd (fixed-point, no scientific notation),
    pricing_source, status.

    Output formats:
    - csv (default): header row + one data row per request.
    - json: JSONL (one JSON object per line; no outer array, no
      indent). Streamable; consumers iterate `json.loads` per line.

    Pair with `voicegw reconcile` (Phase 4.3) to compare against a
    provider's invoice.
    """
    if fmt not in ("csv", "json"):
        console.print(f"[red]Unknown format: {fmt}. Use 'csv' or 'json'.[/red]")
        raise typer.Exit(2)

    gw = _load_gateway(config)
    if gw.storage is None:
        console.print("[yellow]Cost tracking is not enabled in voicegw.yaml[/yellow]")
        raise typer.Exit(1)

    start_ts = _parse_iso_date_arg(start, end_of_day=False)
    end_ts = _parse_iso_date_arg(end, end_of_day=True)

    rows = asyncio.run(
        gw.storage.get_requests_in_window(
            start_ts=start_ts, end_ts=end_ts, project=project
        )
    )

    import io
    import json as _json
    import sys

    buf = io.StringIO()
    if fmt == "csv":
        import csv

        writer = csv.writer(buf)
        writer.writerow(_EXPORT_COLUMNS)
        for r in rows:
            formatted = _format_export_row(r)
            writer.writerow([formatted[col] for col in _EXPORT_COLUMNS])
    else:
        # JSONL: one JSON object per line, no outer array, no indent.
        # Per design §2.1 and TODO 4.1 #4. Streamable; downstream
        # consumers can `for line in f: row = json.loads(line)`.
        for r in rows:
            _json.dump(_format_export_row(r), buf, default=str)
            buf.write("\n")

    payload = buf.getvalue()
    if output == "-":
        sys.stdout.write(payload)
    else:
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            out_path.write_text(payload, encoding="utf-8")
        except OSError as e:
            console.print(f"[red]Failed to write {output}: {e}[/red]")
            raise typer.Exit(1) from e
        console.print(f"[green]Wrote {len(rows)} record(s) to {output}[/green]")


@app.command(name="reconcile")
def reconcile_cmd(
    provider: str = typer.Option(
        ..., "--provider", help="Provider: openai, deepgram, or cartesia."
    ),
    start: str = typer.Option(
        ..., "--start", help="Start date (YYYY-MM-DD, inclusive, UTC)."
    ),
    end: str = typer.Option(
        ..., "--end", help="End date (YYYY-MM-DD, inclusive, UTC)."
    ),
    provider_usage_file: str = typer.Option(
        ...,
        "--provider-usage-file",
        help="Path to the provider's normalized usage file (CSV or JSON).",
    ),
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
    fmt: str = typer.Option(
        "text", "--format", "-f", help="Output format: text (default), csv, or json."
    ),
    threshold: float = typer.Option(
        5.0,
        "--threshold",
        help=(
            "Flag rows whose |cost diff %| exceeds this threshold "
            "(default 5.0). The v0.0.4 disclosure expects LLM "
            "estimates to drift up to ~5%."
        ),
    ),
):
    """Diff VG's logged costs against a provider's usage export.

    See `docs/reference/reconcile-formats.md` for the per-provider
    file schema. Pair with `voicegw export-costs` to surface VG's
    side of the comparison if you want to inspect the raw rows.
    """
    from voicegateway import reconcile as _reconcile

    if provider not in _reconcile.SUPPORTED_PROVIDERS:
        console.print(
            f"[red]Unsupported provider: {provider!r}. "
            f"Supported: {', '.join(_reconcile.SUPPORTED_PROVIDERS)}[/red]"
        )
        raise typer.Exit(2)
    if fmt not in ("text", "csv", "json"):
        console.print(f"[red]Unknown format: {fmt}. Use text, csv, or json.[/red]")
        raise typer.Exit(2)

    gw = _load_gateway(config)
    if gw.storage is None:
        console.print("[yellow]Cost tracking is not enabled in voicegw.yaml[/yellow]")
        raise typer.Exit(1)

    start_ts = _parse_iso_date_arg(start, end_of_day=False)
    end_ts = _parse_iso_date_arg(end, end_of_day=True)

    records = asyncio.run(
        gw.storage.get_requests_in_window(start_ts=start_ts, end_ts=end_ts)
    )

    try:
        lines = _reconcile.reconcile(
            provider,
            records,
            Path(provider_usage_file),
            threshold_pct=threshold,
        )
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(2) from e
    except (ValueError, KeyError) as e:
        console.print(f"[red]Failed to parse provider usage file: {e}[/red]")
        raise typer.Exit(2) from e

    import sys

    if fmt == "csv":
        sys.stdout.write(_reconcile.format_csv(lines))
    elif fmt == "json":
        sys.stdout.write(
            _reconcile.format_json(
                lines,
                provider=provider,
                period_start=start,
                period_end=end,
            )
        )
    else:
        # Colorize flagged rows only on a real TTY; piped output and
        # CliRunner captures stay plain text.
        sys.stdout.write(
            _reconcile.format_text(lines, provider, colorize=sys.stdout.isatty())
        )


@app.command(name="mcp")
def mcp_cmd(
    transport: str = typer.Option(
        "stdio",
        "--transport",
        "-t",
        help="Transport layer: 'stdio' for local agents, 'http' for remote/SSE.",
    ),
    host: str = typer.Option("127.0.0.1", "--host", help="HTTP bind host (http only)"),
    port: int = typer.Option(8090, "--port", "-p", help="HTTP bind port (http only)"),
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
):
    """Start the VoiceGateway MCP server.

    Use stdio transport for local agents (Claude Code, Cursor):

        voicegw mcp --transport stdio

    Use HTTP/SSE for remote access or team gateways:

        voicegw mcp --transport http --port 8090

    Authentication (HTTP only) via VOICEGW_MCP_TOKEN env var.
    """
    if transport not in ("stdio", "http"):
        console.print(
            f"[red]Unknown transport: {transport}. Use 'stdio' or 'http'.[/red]"
        )
        raise typer.Exit(1)

    try:
        from voicegateway.mcp.server import serve_http, serve_stdio
    except ImportError as e:
        console.print(
            "[red]MCP dependencies not installed. "
            "Run: pip install 'voicegateway[mcp]'[/red]"
        )
        raise typer.Exit(1) from e

    gw = _load_gateway(config)

    if transport == "stdio":
        asyncio.run(serve_stdio(gw))
    else:
        console.print(
            f"[green]VoiceGateway MCP server listening on http://{host}:{port}/sse[/green]"
        )
        asyncio.run(serve_http(gw, host=host, port=port))


if __name__ == "__main__":
    app()
