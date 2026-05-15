"""Helpers used by two or more ``voicegateway.cli`` command modules.

``_load_gateway`` is now a thin shim over ``BaseCli.require_gateway``.
New code should call ``BaseCli().require_gateway(config_path)`` directly;
this wrapper survives for callers that still import from
``utils.cli._shared``.
"""

from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway


def _load_gateway(config_path: str | None) -> Gateway:
    """Backwards-compatible wrapper. Prefer ``BaseCli().require_gateway``."""
    from voicegateway.cli.base_cli import BaseCli

    return BaseCli().require_gateway(config_path)


def _parse_iso_date_arg(value: str, *, end_of_day: bool) -> float:
    """Parse ``YYYY-MM-DD`` into a UTC timestamp."""
    from voicegateway.cli.base_cli import BaseCli

    try:
        d = _dt.datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=_dt.UTC)
    except ValueError:
        BaseCli().fail(f"Invalid date {value!r}: expected YYYY-MM-DD", code=2)
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
    """Canonical config home: ``~/.config/voicegateway``."""
    return Path.home() / ".config" / "voicegateway"
