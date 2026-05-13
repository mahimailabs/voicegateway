"""Factory that picks ``HttpClient`` vs ``LocalClient`` per launch mode.

Locked decision 5 (``.agents/design.md`` section 4): when ``local=True``,
the factory ALWAYS returns :class:`LocalClient`, regardless of whether
the daemon is reachable. The persistent ``[Local mode]`` chip in the
header is the visible signal that the override applied; that is how
the user catches a stale ``--local`` baked into a shell alias instead
of being silently re-routed through the daemon.

Polling cadence resolution mirrors decision 1 + decision 2: when the
caller does not pass ``poll`` (the Typer ``--poll`` flag is
``None``), the factory selects the mode-appropriate default
(1.0 s for Gateway mode, 5.0 s for Local mode). Pass an explicit
``poll`` value to override either default.
"""

from __future__ import annotations

import os
from pathlib import Path

from voicegateway.cli.tui.data import MetricsClient
from voicegateway.cli.tui.data.http import HttpClient
from voicegateway.cli.tui.data.local import LocalClient

_GATEWAY_POLL_DEFAULT = 1.0
_LOCAL_POLL_DEFAULT = 5.0


def make_client(
    *,
    local: bool,
    url: str,
    token: str | None = None,
    poll: float | None = None,
    db_path: str | Path | None = None,
) -> MetricsClient:
    """Return the right :class:`MetricsClient` for the launch mode.

    Args:
        local: when True, return :class:`LocalClient` unconditionally
            (locked decision 5). When False, return :class:`HttpClient`
            against ``url``.
        url: daemon HTTP URL. Required even when ``local=True`` so the
            Typer command can stay symmetric and so the factory does
            not enforce mutually-exclusive flag semantics that would
            collide with the ``[Local mode]`` chip's "override is
            visible" rule.
        token: optional bearer token for daemon write paths
            (``test_provider``). Empty string and ``None`` are
            equivalent; both produce no ``Authorization`` header.
        poll: poll cadence in seconds. ``None`` selects the
            mode-appropriate default: 1.0 s for Gateway mode (locked
            decision 1) and 5.0 s for Local mode (locked decision 2).
        db_path: SQLite file location for Local mode. ``None`` resolves
            to ``$VOICEGW_DB_PATH`` if set, otherwise the v0.0.5
            canonical path ``~/.config/voicegateway/voicegw.db``.
            Ignored when ``local=False``.

    Returns:
        Either :class:`HttpClient` or :class:`LocalClient`. Both
        structurally satisfy :class:`MetricsClient`; screens never
        branch on which one is live.
    """
    if local:
        resolved_db = _resolve_db_path(db_path)
        return LocalClient(
            db_path=resolved_db,
            poll_seconds=(poll if poll is not None else _LOCAL_POLL_DEFAULT),
        )
    return HttpClient(
        url=url,
        token=token,
        poll_seconds=(poll if poll is not None else _GATEWAY_POLL_DEFAULT),
    )


def _resolve_db_path(explicit: str | Path | None) -> Path:
    """Resolve the SQLite path used in Local mode.

    Order of precedence (matches :class:`voicegateway.core.gateway.Gateway`'s
    ``__init__`` exactly): ``$VOICEGW_DB_PATH``, then the explicit
    argument (which :func:`run` sources from
    ``voicegw.yaml``'s ``cost_tracking.db_path``), then the v0.0.5
    canonical path ``~/.config/voicegateway/voicegw.db``.

    The env var sits at the top so it stays a usable debugging escape
    hatch even when a yaml config pins a different path. Without this
    ordering, a user could not point the TUI at a one-off DB without
    editing their config.
    """
    env_value = os.environ.get("VOICEGW_DB_PATH")
    if env_value:
        return Path(env_value).expanduser()
    if explicit is not None:
        return Path(explicit).expanduser()
    return (Path.home() / ".config" / "voicegateway" / "voicegw.db").expanduser()


__all__ = ["make_client"]
