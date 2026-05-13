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

Important: this module imports ONLY ``typer``, ``httpx``, and the
CLI's internal Typer instance at module top. The ``TUIApp``
re-export (used by tests and external Python callers) is wired via
a PEP 562 module-level ``__getattr__`` so importing this package
does not pull in :mod:`textual`. ``textual`` is in the optional
``[tui]`` extra; a regression here would break every ``voicegw``
command on installs that did not opt into the extra, because
``voicegateway.cli.__init__`` side-effect-imports this module to
register the Typer command.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import typer

# Alias the Typer instance to ``cli_app`` because ``voicegateway.cli.
# tui`` carries an ``app`` submodule (``app.py``); importing
# ``TUIApp`` from it sets ``app = <module>`` on this package's
# namespace, which would overwrite a top-level ``app = Typer(...)``
# binding regardless of import order. The alias keeps both names
# unambiguous and survives any formatter-driven import reordering.
from voicegateway.cli._app import app as cli_app
from voicegateway.cli._app import console

if TYPE_CHECKING:  # pragma: no cover
    from voicegateway.cli.tui.app import TUIApp
    from voicegateway.core.config import GatewayConfig


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

    Loads ``voicegw.yaml`` once (when reachable) and threads its
    values to the factory: ``serve.host`` / ``serve.port`` resolves
    the daemon URL fallback, and ``cost_tracking.db_path`` becomes
    the Local-mode SQLite path when set. Then instantiates the
    right :class:`MetricsClient` via
    :func:`voicegateway.cli.tui.data.factory.make_client` and runs
    :class:`TUIApp` to enter the Textual event loop.

    ``history_limit`` and ``theme`` are accepted today and stored
    on the launched app so Phases 3-6 (screens) and Phase 8 (TCSS)
    can read them without reaching back into the Typer parser; the
    apply-side wiring lands in those phases.
    """
    # Lazy imports: ``voicegateway.cli.tui.app`` pulls in ``textual``,
    # which is optional (the ``[tui]`` extra). Touching it inside
    # ``run`` keeps the package import light for the rest of the CLI.
    from voicegateway.cli.tui.app import TUIApp
    from voicegateway.cli.tui.data.factory import make_client

    cfg = _try_load_config(config)
    resolved_url = url if url is not None else _url_from_config(cfg)
    # In Local mode, prefer the yaml-configured ``cost_tracking.db_path``
    # so a user who pointed their daemon at a non-default SQLite file
    # sees the same history when launching ``voicegw tui --local``.
    # Factory's precedence (env > explicit > default) means
    # ``VOICEGW_DB_PATH`` still wins for debugging even when this
    # threading is active.
    resolved_db_path = _db_path_from_config(cfg) if local else None
    client = make_client(
        local=local,
        url=resolved_url,
        token=token,
        poll=poll,
        db_path=resolved_db_path,
    )
    app_instance = TUIApp(client=client, is_local=local)
    # Stash the cosmetic flags on the app so screens can read them
    # without re-resolving from Typer state. Phase 3-6 swaps Static
    # placeholders for real screens that read these.
    app_instance._history_limit = history_limit  # type: ignore[attr-defined]
    app_instance._theme = theme  # type: ignore[attr-defined]
    app_instance.run()


def _try_load_config(config_path: str | None) -> GatewayConfig | None:
    """Best-effort load of ``voicegw.yaml``.

    Returns the :class:`GatewayConfig` instance when the file resolves
    cleanly, ``None`` otherwise. Swallowing the error here keeps
    ``voicegw tui --local`` working when no config is configured at
    all (the canonical-path defaults in the factory take over).
    """
    try:
        from voicegateway.core.config import GatewayConfig

        return GatewayConfig.load(config_path)
    except Exception:  # noqa: BLE001
        return None


def _url_from_config(cfg: GatewayConfig | None) -> str:
    """Daemon URL fallback derived from ``serve.host`` / ``serve.port``.

    ``0.0.0.0`` (the default bind, "all interfaces") is rewritten to
    ``127.0.0.1`` because a TUI client cannot connect to
    ``0.0.0.0``. Returns ``http://127.0.0.1:8080`` when ``cfg`` is
    ``None``.
    """
    if cfg is None:
        return "http://127.0.0.1:8080"
    serve = cfg.serve or {}
    host = serve.get("host", "0.0.0.0")
    port = serve.get("port", 8080)
    client_host = "127.0.0.1" if host == "0.0.0.0" else host
    return f"http://{client_host}:{port}"


def _db_path_from_config(cfg: GatewayConfig | None) -> str | None:
    """``cost_tracking.db_path`` from voicegw.yaml, or ``None``.

    ``None`` signals "no yaml override" so the factory's resolver
    can fall through to ``$VOICEGW_DB_PATH`` and the canonical path
    (matches ``voicegateway.core.gateway.Gateway.__init__``'s
    precedence).
    """
    if cfg is None:
        return None
    value = cfg.cost_tracking.get("db_path")
    return str(value) if value else None


def _resolve_default_url(config_path: str | None) -> str:
    """Back-compat shim: load the config and derive the URL fallback.

    Used by :func:`tui_cmd` which runs the preflight before delegating
    to :func:`run`. Kept as a thin wrapper around the new helpers so
    the call-site reads naturally without re-resolving the config.
    """
    return _url_from_config(_try_load_config(config_path))


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
    resolved_url = url if url is not None else _resolve_default_url(config)
    if not local and not _preflight_daemon_reachable(resolved_url, token):
        console.print(
            f"[red]Daemon at {resolved_url} is not reachable.[/red]\n"
            "Start it with `voicegw start` (after `voicegw onboard "
            "--install-daemon` if you have not yet), or run "
            "`voicegw tui --local` for read-only postmortem "
            "inspection of the SQLite call DB."
        )
        raise typer.Exit(1)
    try:
        run(
            local=local,
            url=resolved_url,
            token=token,
            poll=poll,
            history_limit=history_limit,
            theme=theme,
            config=config,
        )
    except Exception as exc:  # noqa: BLE001
        # Any launch-time failure surfaces as a clean exit-1 with a
        # useful pointer rather than a Python traceback. The
        # daemon-reachable preflight above catches the most common
        # cause; this fallback covers the rest (corrupt SQLite in
        # Local mode, textual import failure, etc.).
        console.print(f"[red]Failed to launch TUI:[/red] {exc}")
        raise typer.Exit(1) from exc


def _preflight_daemon_reachable(
    url: str, token: str | None, timeout: float = 2.0
) -> bool:
    """Return ``True`` when the daemon at ``url`` answers ``/health``.

    Synchronous probe so the daemon-down message can render before
    Textual takes over the terminal. ``timeout`` defaults to 2 s --
    long enough for a localhost daemon to respond, short enough that
    a typo in ``--url`` does not stall the launch.
    """

    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = httpx.get(
            f"{url.rstrip('/')}/health",
            timeout=timeout,
            headers=headers,
        )
    except Exception:  # noqa: BLE001
        return False
    return response.status_code == 200


def __getattr__(name: str) -> Any:
    """Lazy module-level re-export of :class:`TUIApp` (PEP 562).

    ``from voicegateway.cli.tui import TUIApp`` triggers this handler
    and only then imports :mod:`voicegateway.cli.tui.app`, which in
    turn imports :mod:`textual`. Plain ``import voicegateway.cli.tui``
    (the side-effect import from ``voicegateway.cli.__init__``) does
    not touch ``textual`` at all, so installs without the ``[tui]``
    extra keep working for every non-TUI ``voicegw`` command.
    """
    if name == "TUIApp":
        from voicegateway.cli.tui.app import TUIApp as _TUIApp

        return _TUIApp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["TUIApp", "run", "tui_cmd"]
