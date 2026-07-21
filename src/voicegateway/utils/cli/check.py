"""Helpers for ``voicegw check``: a framework-agnostic pipeline check.

``check`` verifies the path that actually matters in the framework-agnostic
model: it drives ONE synthetic instrumented request through the metering
middleware and asserts a request row + a session row land in storage, proving
cost metering, storage, and session correlation work end to end. It does NOT
construct providers from config; you bring your own instances and call
``attach()`` / ``guard()``.
"""

from __future__ import annotations

from typing import Any

from rich.table import Table

from voicegateway.core import active_project as project_module
from voicegateway.core import gateway_factory as factory
from voicegateway.inference.session.context import (
    get_or_create_session_id,
    get_session_id,
)

# A real, voice-prices-priced model id so the synthetic request exercises cost
# resolution. The pass condition is the rows landing, not a cost threshold.
_CHECK_MODEL_ID = "openai/gpt-4o-mini"


def check_active_project(gw: Any) -> str | None:
    """Pick a project: ``default_project``, else a named project, else ``default``."""
    if gw.config.default_project:
        return str(gw.config.default_project)
    named = [p for p in gw.config.projects if p != "default"]
    if named:
        return str(sorted(named)[0])
    if "default" in gw.config.projects:
        return "default"
    return None


async def run_check(gw: Any, project: str, add: Any) -> None:
    """Drive one synthetic instrumented request and assert it lands.

    IMPORTANT: the session id is created, the request is written, and the rows
    are read back all inside THIS coroutine (one event loop). The session
    ContextVar does not cross ``asyncio.run`` boundaries; splitting these would
    record ``session_id=None`` and the correlation check would false-fail (the
    exact bug this command was rewritten to avoid).
    """
    # Import the instrumented middleware lazily: it pulls the livekit runtime, so
    # importing it at module top would make the whole CLI require livekit (the
    # eager-import trap). `check` needs it, the rest of the CLI does not.
    try:
        from voicegateway.middleware.instrumented_provider_middleware import (
            wrap_provider,
        )
    except ImportError as exc:
        add(
            "request landed",
            False,
            f"livekit runtime not installed ({exc}). "
            "Install voicegateway[livekit] to run check.",
        )
        return

    class _StubLLM:
        """Minimal stand-in the LLM wrapper accepts (it only calls ``.on``)."""

        def on(self, _event: str, _cb: Any) -> None:
            pass

    factory._gateway = gw
    project_module.reset_project()
    project_module.set_project(project)

    # Establish the session id in this loop so the write records it and the
    # read-back finds the row.
    get_or_create_session_id()

    try:
        instance = wrap_provider(
            instance=_StubLLM(),
            modality="llm",
            model_id=_CHECK_MODEL_ID,
            provider="openai",
            project=project,
            cost_tracker=gw._cost_tracker,
            storage=gw._storage,
            metering=True,
        )
    except Exception as exc:  # noqa: BLE001
        add("request landed", False, f"{type(exc).__name__}: {exc}")
        return

    # `_log_request` is best-effort (it swallows storage errors), so we assert on
    # the READ-BACK below, not on this call not raising.
    await instance._log_request(input_units=1.0)

    sid = get_session_id()
    if sid is None:
        add("request landed", False, "No session_id in context after the request.")
        return

    session = await gw.storage.get_session(sid)
    if session is None:
        add("request landed", False, f"no request/session row landed for {sid}.")
        return

    add(
        "request landed",
        int(session.get("request_count", 0)) >= 1,
        f"session {sid} has request_count={session.get('request_count', 0)}.",
    )

    modalities = set(session.get("modalities", []))
    if "llm" not in modalities:
        add(
            "session correlation",
            False,
            f"session {sid} missing llm; carries {sorted(modalities)}.",
        )
        return
    add(
        "session correlation",
        True,
        f"{sid}: modalities={','.join(sorted(modalities))}, "
        f"requests={session.get('request_count', 0)}, "
        f"cost=${session.get('total_cost_usd', 0.0):.6f}.",
    )


def _print_check_report(rows: list[tuple[str, bool, str]]) -> None:
    # Imported lazily so importing this module does not trigger the
    # voicegateway.cli package __init__ (which imports check_cli, which imports
    # this module: a circular import if a test imports check.py first).
    from voicegateway.cli._app import console

    table = Table(title="VoiceGateway check")
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
            "\n[green]All checks passed.[/green] A synthetic request flowed "
            "through metering and storage and correlated to a session. Add "
            "[bold]voicegateway.attach(session, project=...)[/bold] to your "
            "agent to meter real calls."
        )
