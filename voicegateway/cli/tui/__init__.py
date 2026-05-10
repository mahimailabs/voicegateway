"""VoiceGateway terminal UI package (v0.1.1).

Re-exports the Phase-2 :class:`TUIApp` and exposes the Typer ``tui``
command so the existing ``voicegw`` console script gains a fourth-tab
companion to the v0.1.0 surface (``status``, ``onboard``, ``doctor``,
...).

Two entry points live side-by-side:

- :func:`run` -- the importable launcher used by tests and any
  Python caller that wants to drive the TUI without going through
  Typer (e.g., a Pilot-based smoke test that constructs ``TUIApp``
  via :func:`run` rather than poking the Typer parser).
- :func:`tui_cmd` -- the Typer command (``voicegw tui``). Decorated
  with ``@cli_app.command(name="tui")`` so the existing
  ``voicegateway.cli`` package gains the command via a side-effect
  import in ``voicegateway/cli/__init__.py``.
"""

from __future__ import annotations

import typer

# Alias the Typer instance to ``cli_app`` because ``voicegateway.cli.
# tui`` carries an ``app`` submodule (``app.py``); importing
# ``TUIApp`` from it sets ``app = <module>`` on this package's
# namespace, which would overwrite a top-level ``app = Typer(...)``
# binding regardless of import order. The alias keeps both names
# unambiguous and survives any formatter-driven import reordering.
from voicegateway.cli._app import app as cli_app
from voicegateway.cli._app import console
from voicegateway.cli.tui.app import TUIApp


def run(
    *,
    local: bool = False,
    url: str | None = None,
    token: str | None = None,
    poll: float | None = None,
    history_limit: int = 100,
    theme: str = "brand",
    config: str | None = None,
) -> None:
    """Launch the TUI programmatically.

    Resolves the daemon URL from voicegw.yaml when ``url`` is not
    supplied, instantiates the right :class:`MetricsClient` via
    :func:`voicegateway.cli.tui.data.factory.make_client`, then runs
    :class:`TUIApp` to enter the Textual event loop.

    ``history_limit`` and ``theme`` are accepted today and stored
    on the launched app so Phases 3-6 (screens) and Phase 8 (TCSS)
    can read them without reaching back into the Typer parser; the
    apply-side wiring lands in those phases.
    """
    from voicegateway.cli.tui.data.factory import make_client

    resolved_url = url if url is not None else _resolve_default_url(config)
    client = make_client(local=local, url=resolved_url, token=token, poll=poll)
    app_instance = TUIApp(client=client, is_local=local)
    # Stash the cosmetic flags on the app so screens can read them
    # without re-resolving from Typer state. Phase 3-6 swaps Static
    # placeholders for real screens that read these.
    app_instance._history_limit = history_limit  # type: ignore[attr-defined]
    app_instance._theme = theme  # type: ignore[attr-defined]
    app_instance.run()


def _resolve_default_url(config_path: str | None) -> str:
    """Default ``--url`` resolution.

    Pulls ``serve.host`` / ``serve.port`` from voicegw.yaml; falls
    back to ``http://127.0.0.1:8080`` when the config does not
    load. ``0.0.0.0`` (the default bind, "all interfaces") is
    rewritten to ``127.0.0.1`` because a TUI client cannot connect
    to ``0.0.0.0``.
    """
    try:
        from voicegateway.core.config import GatewayConfig

        cfg = GatewayConfig.load(config_path)
        serve = cfg.serve or {}
        host = serve.get("host", "0.0.0.0")
        port = serve.get("port", 8080)
    except Exception:
        return "http://127.0.0.1:8080"
    client_host = "127.0.0.1" if host == "0.0.0.0" else host
    return f"http://{client_host}:{port}"


@cli_app.command(name="tui")
def tui_cmd(
    local: bool = typer.Option(
        False,
        "--local",
        help=(
            "Use Local mode: read SQLite directly without going "
            "through the daemon. Useful for postmortem inspection "
            "when the daemon is down. Locked decision 5: --local "
            "wins regardless of whether the daemon is reachable."
        ),
    ),
    url: str = typer.Option(
        None,
        "--url",
        help=(
            "Daemon URL. Defaults to http://<serve.host>:<serve.port> "
            "from voicegw.yaml (with 0.0.0.0 rewritten to 127.0.0.1)."
        ),
    ),
    token: str = typer.Option(
        None,
        "--token",
        help=(
            "Bearer token for daemon write paths "
            "(e.g., the Providers tab's `t` test shortcut)."
        ),
    ),
    history_limit: int = typer.Option(
        100,
        "--history-limit",
        help="Initial row count for the Sessions and Logs tabs.",
    ),
    theme: str = typer.Option(
        "brand",
        "--theme",
        help="Color theme. `brand` is the v0.1.1 default.",
    ),
    poll: float = typer.Option(
        None,
        "--poll",
        help=(
            "Polling cadence in seconds. Default: 1.0 for Gateway "
            "mode, 5.0 for Local mode (locked decisions 1+2)."
        ),
    ),
    config: str = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to voicegw.yaml. Defaults to the standard search.",
    ),
) -> None:
    """Launch the four-tab terminal UI (Sessions / Costs / Logs / Providers)."""
    try:
        run(
            local=local,
            url=url,
            token=token,
            poll=poll,
            history_limit=history_limit,
            theme=theme,
            config=config,
        )
    except Exception as exc:  # noqa: BLE001
        # Any launch-time failure surfaces as a clean exit-1 with a
        # useful pointer rather than a Python traceback. Phase 2's
        # "daemon-down" message-on-no-route bullet refines this in
        # the next iteration with a more specific hint when the user
        # is in Gateway mode and the daemon is unreachable.
        console.print(f"[red]Failed to launch TUI:[/red] {exc}")
        raise typer.Exit(1) from exc


__all__ = ["TUIApp", "run", "tui_cmd"]
