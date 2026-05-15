"""VoiceGateway CLI package.

Every command module follows the ``<name>_cli.py`` convention and uses
:class:`BaseCli` for shared output, gateway loading, storage-required-
or-die, and exit helpers. New commands should follow this pattern::

    # cli/example_cli.py
    from voicegateway.cli import BaseCli
    from voicegateway.cli._app import app

    _cli = BaseCli()

    @app.command()
    def example(config: str | None = None) -> None:
        gw = _cli.require_gateway(config)
        storage = _cli.require_storage(gw)
        ...

Subclassing :class:`BaseCli` is supported but not required; Typer is
function-first by design.
"""

from __future__ import annotations

from voicegateway.cli import brand_cli as _brand  # noqa: F401, E402
from voicegateway.cli import costs_cli as _costs  # noqa: F401, E402
from voicegateway.cli import dashboard_cli as _dashboard  # noqa: F401, E402
from voicegateway.cli import doctor_cli as _doctor  # noqa: F401, E402
from voicegateway.cli import export_costs_cli as _export_costs  # noqa: F401, E402
from voicegateway.cli import guardrails_cli as _guardrails  # noqa: F401, E402
from voicegateway.cli import init_cli as _init  # noqa: F401, E402
from voicegateway.cli import lifecycle_cli as _lifecycle  # noqa: F401, E402
from voicegateway.cli import logs_cli as _logs  # noqa: F401, E402
from voicegateway.cli import mcp_cli as _mcp  # noqa: F401, E402
from voicegateway.cli import migrate_cli as _migrate  # noqa: F401, E402
from voicegateway.cli import onboard_cli as _onboard  # noqa: F401, E402
from voicegateway.cli import projects_cli as _projects  # noqa: F401, E402
from voicegateway.cli import reconcile_cli as _reconcile  # noqa: F401, E402
from voicegateway.cli import replay_cli as _replay  # noqa: F401, E402
from voicegateway.cli import rotate_secret_cli as _rotate_secret  # noqa: F401, E402
from voicegateway.cli import route_cli as _route  # noqa: F401, E402
from voicegateway.cli import serve_cli as _serve  # noqa: F401, E402
from voicegateway.cli import smoke_test_cli as _smoke_test  # noqa: F401, E402
from voicegateway.cli import status_cli as _status  # noqa: F401, E402
from voicegateway.cli import tenant_cli as _tenant  # noqa: F401, E402
from voicegateway.cli import tui as _tui  # noqa: F401, E402
from voicegateway.cli._app import app, console
from voicegateway.cli.base_cli import BaseCli

__all__ = ["BaseCli", "app", "console"]
