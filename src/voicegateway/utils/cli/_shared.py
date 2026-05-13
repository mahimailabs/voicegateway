"""Helpers used by two or more ``voicegateway.cli`` command modules.

Anything used by only one command lives in
``voicegateway.utils.cli.<command>`` instead.
"""

from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from voicegateway.cli._app import console

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway


def _load_gateway(config_path: str | None) -> Gateway:
    """Build a Gateway from ``config_path`` (or the default search path).

    Exits with a Rich-styled error message and exit code 1 on any
    config-load failure so each cli command can stay focused on its
    own logic.
    """
    from voicegateway.core.gateway import Gateway

    try:
        return Gateway(config_path=config_path)
    except Exception as e:
        console.print(f"[red]Error loading config: {e}[/red]")
        raise typer.Exit(1) from e


def _parse_iso_date_arg(value: str, *, end_of_day: bool) -> float:
    """Parse ``YYYY-MM-DD`` into a UTC timestamp.

    ``end_of_day=True`` advances by 24 h so callers can use the
    parsed value as an exclusive upper bound on a daily range.
    """
    try:
        d = _dt.datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=_dt.UTC)
    except ValueError as e:
        console.print(f"[red]Invalid date {value!r}: expected YYYY-MM-DD[/red]")
        raise typer.Exit(2) from e
    if end_of_day:
        d += _dt.timedelta(days=1)
    return d.timestamp()


def _auth_headers() -> dict[str, str]:
    """Return Bearer auth headers when ``VOICEGW_API_KEY`` is set."""
    token = os.environ.get("VOICEGW_API_KEY", "").strip()
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _config_home() -> Path:
    """v0.0.5's canonical config home: ``~/.config/voicegateway``."""
    return Path.home() / ".config" / "voicegateway"
