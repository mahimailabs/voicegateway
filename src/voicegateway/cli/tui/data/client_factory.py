"""Factory that picks ``HttpClient`` vs ``LocalClient`` per launch mode."""

from __future__ import annotations

import os
from pathlib import Path

from voicegateway.cli.tui.data import MetricsClient
from voicegateway.cli.tui.data.http_client import HttpClient
from voicegateway.cli.tui.data.local_client import LocalClient

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
    """Return the right :class:`MetricsClient` for the launch mode."""
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
    """Resolve the SQLite path used in Local mode."""
    env_value = os.environ.get("VOICEGW_DB_PATH")
    if env_value:
        return Path(env_value).expanduser()
    if explicit is not None:
        return Path(explicit).expanduser()
    return (Path.home() / ".config" / "voicegateway" / "voicegw.db").expanduser()


__all__ = ["make_client"]
