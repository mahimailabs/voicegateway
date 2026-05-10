"""``voicegw smoke-test`` command.

Carved out of voicegateway/cli/_legacy.py during the v0.1.0 section-2
refactor. Runs the v0.0.5 inference pipeline end-to-end without
LiveKit so AC-3 / AC-5 evidence stays reproducible:

  - structural pipeline checks against stubbed LK plugins, then
  - optional ``--live`` health-check probes against real provider
    APIs.

Owns its three private helpers (`_smoke_active_project`,
`_smoke_pick_models`, `_print_smoke_report`), the two async runners
(`_run_smoke_pipeline_checks`, `_run_smoke_health_checks`), and the
`_SMOKE_MODALITIES` lookup tuple.
"""

from __future__ import annotations

import asyncio
from typing import Any

import typer
from rich.table import Table

# See voicegateway/cli/init.py for the rationale on importing
# ``app`` and ``console`` from ``_legacy`` rather than from the
# package during the v0.1.0 migration period.
from voicegateway.cli._legacy import _load_gateway, app, console


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
) -> None:
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
