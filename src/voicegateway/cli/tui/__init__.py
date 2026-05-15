"""VoiceGateway terminal UI package.

The Typer entry point (``voicegw tui``) lives in
:mod:`voicegateway.cli.tui_cli`. This package contains the Textual
``App`` subclass plus its screens, widgets, data clients, and styles.

Public surface:

- :func:`run`: launch the TUI programmatically (used by ``tui_cmd``).
- :class:`TUIApp`: the Textual ``App`` subclass, available as
  ``voicegateway.cli.tui.TUIApp`` via lazy attribute access.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
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
    """Launch the TUI programmatically."""
    from voicegateway.cli.tui.app import TUIApp
    from voicegateway.cli.tui.data.factory import make_client
    from voicegateway.cli.tui_cli import _try_load_config, _url_from_config

    cfg = _try_load_config(config)
    resolved_url = url if url is not None else _url_from_config(cfg)
    resolved_db_path = _db_path_from_config(cfg) if local else None

    client = make_client(
        local=local,
        url=resolved_url,
        token=token,
        poll=poll,
        db_path=resolved_db_path,
    )
    app_instance = TUIApp(client=client, is_local=local)
    app_instance._history_limit = history_limit  # type: ignore[attr-defined]
    app_instance._theme = theme  # type: ignore[attr-defined]
    app_instance.run()


def _db_path_from_config(cfg: Any) -> str | None:
    """``cost_tracking.db_path`` from voicegw.yaml, or ``None``."""
    if cfg is None:
        return None
    value = cfg.cost_tracking.get("db_path")
    return str(value) if value else None


def __getattr__(name: str) -> Any:
    """Lazy module-level re-export of :class:`TUIApp` (PEP 562)."""
    if name == "TUIApp":
        from voicegateway.cli.tui.app import TUIApp as _TUIApp

        return _TUIApp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["TUIApp", "run"]
