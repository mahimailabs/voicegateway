"""VoiceGateway CLI package."""

from __future__ import annotations

from voicegateway.cli import brand as _brand  # noqa: F401, E402
from voicegateway.cli import costs as _costs  # noqa: F401, E402
from voicegateway.cli import dashboard as _dashboard  # noqa: F401, E402
from voicegateway.cli import doctor as _doctor  # noqa: F401, E402
from voicegateway.cli import export_costs as _export_costs  # noqa: F401, E402
from voicegateway.cli import guardrails as _guardrails  # noqa: F401, E402
from voicegateway.cli import init as _init  # noqa: F401, E402
from voicegateway.cli import lifecycle as _lifecycle  # noqa: F401, E402
from voicegateway.cli import logs as _logs  # noqa: F401, E402
from voicegateway.cli import mcp as _mcp  # noqa: F401, E402
from voicegateway.cli import migrate as _migrate  # noqa: F401, E402
from voicegateway.cli import onboard as _onboard  # noqa: F401, E402
from voicegateway.cli import projects as _projects  # noqa: F401, E402
from voicegateway.cli import reconcile as _reconcile  # noqa: F401, E402
from voicegateway.cli import replay as _replay  # noqa: F401, E402
from voicegateway.cli import rotate_secret as _rotate_secret  # noqa: F401, E402
from voicegateway.cli import route as _route  # noqa: F401, E402
from voicegateway.cli import serve as _serve  # noqa: F401, E402
from voicegateway.cli import smoke_test as _smoke_test  # noqa: F401, E402
from voicegateway.cli import status as _status  # noqa: F401, E402
from voicegateway.cli import tenant as _tenant  # noqa: F401, E402
from voicegateway.cli import tui as _tui  # noqa: F401, E402
from voicegateway.cli._app import app, console

__all__ = ["app", "console"]
