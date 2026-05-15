"""Helpers for ``voicegateway.cli.smoke_test``."""

from __future__ import annotations

from typing import Any

from rich.table import Table

from voicegateway.cli._app import console
from voicegateway.core import registry as _registry
from voicegateway.core.constants import SMOKE_MODALITIES
from voicegateway.inference import (
    factory,
)
from voicegateway.inference import (
    llm_inference as llm,
)
from voicegateway.inference import project as project_module
from voicegateway.inference import (
    stt_inference as stt,
)
from voicegateway.inference import (
    tts_inference as tts,
)
from voicegateway.inference.session.context import get_session_id


def _smoke_active_project(gw: Any) -> str | None:
    """Pick a project for the smoke test."""
    if gw.config.default_project:
        return str(gw.config.default_project)
    named = [p for p in gw.config.projects if p != "default"]
    if named:
        return str(sorted(named)[0])
    if "default" in gw.config.projects:
        return "default"
    return None


def _smoke_pick_models(gw: Any, project: str) -> dict[str, str]:
    """Return a model_id per modality available to ``project``."""
    proj = gw.config.get_project(project)
    if proj is None:
        return {}
    available_providers: set[str] = set(proj.providers)
    available_providers.update(gw.config.providers)
    # Local providers don't need keys; surface them as available
    # automatically so a fresh install can still smoke-test.
    available_providers.update({"ollama", "whisper", "kokoro", "piper"})

    chosen: dict[str, str] = {}
    for modality, _ in SMOKE_MODALITIES:
        bucket = gw.config.models.get(modality) or {}
        for model_id, model_cfg in bucket.items():
            provider_name = model_cfg.get("provider", model_id.split("/", 1)[0])
            if provider_name in available_providers:
                chosen[modality] = model_id
                break
    return chosen


async def _run_smoke_pipeline_checks(gw: Any, project: str, add) -> None:
    """Construct each modality with stubbed LK plugins, drive a fake"""

    models = _smoke_pick_models(gw, project)

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

    factory._gateway = gw
    project_module.reset_project()
    project_module.set_project(project)

    real_create_provider = _registry.create_provider
    _registry.create_provider = _stub_create  # type: ignore[assignment]

    session_id_holder: dict[str, str | None] = {"sid": None}

    try:
        for modality, label in SMOKE_MODALITIES:
            model_id = models.get(modality)
            if model_id is None:
                add(
                    f"inference.{label}",
                    True,
                    "skipped (no model registered for this modality)",
                )
                continue
            instance: Any
            try:
                if modality == "stt":
                    instance = stt.STT(model_id)
                elif modality == "llm":
                    instance = llm.LLM(model_id)
                else:
                    instance = tts.TTS(model_id)
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
        _registry.create_provider = real_create_provider

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
    expected_modalities = {m for m, _ in SMOKE_MODALITIES if m in models}
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
        if name not in _registry._PROVIDER_REGISTRY:
            continue
        try:
            provider_instance = _registry.create_provider(name, cfg)
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
            "actual end-to-end run, wire [bold]voicegateway.inference[/bold] "
            "into a LiveKit AgentSession and connect to a dev server."
        )
