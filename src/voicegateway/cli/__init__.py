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

import typer

from voicegateway.cli import baseline_cli as _baseline  # noqa: F401, E402
from voicegateway.cli import brand_cli as _brand  # noqa: F401, E402
from voicegateway.cli import calls_cli as _calls  # noqa: F401, E402
from voicegateway.cli import check_cli as _check  # noqa: F401, E402
from voicegateway.cli import costs_cli as _costs  # noqa: F401, E402
from voicegateway.cli import dashboard_cli as _dashboard  # noqa: F401, E402
from voicegateway.cli import doctor_cli as _doctor  # noqa: F401, E402
from voicegateway.cli import export_costs_cli as _export_costs  # noqa: F401, E402
from voicegateway.cli import init_cli as _init  # noqa: F401, E402
from voicegateway.cli import keys_cli as _keys  # noqa: F401, E402
from voicegateway.cli import lifecycle_cli as _lifecycle  # noqa: F401, E402
from voicegateway.cli import loadtest_cli as _loadtest  # noqa: F401, E402
from voicegateway.cli import logs_cli as _logs  # noqa: F401, E402
from voicegateway.cli import mcp_cli as _mcp  # noqa: F401, E402
from voicegateway.cli import migrate_cli as _migrate  # noqa: F401, E402
from voicegateway.cli import onboard_cli as _onboard  # noqa: F401, E402
from voicegateway.cli import prices_cli as _prices  # noqa: F401, E402
from voicegateway.cli import projects_cli as _projects  # noqa: F401, E402
from voicegateway.cli import reconcile_cli as _reconcile  # noqa: F401, E402
from voicegateway.cli import replay_cli as _replay  # noqa: F401, E402
from voicegateway.cli import rotate_secret_cli as _rotate_secret  # noqa: F401, E402
from voicegateway.cli import route_cli as _route  # noqa: F401, E402
from voicegateway.cli import serve_cli as _serve  # noqa: F401, E402
from voicegateway.cli import status_cli as _status  # noqa: F401, E402
from voicegateway.cli._app import app, console
from voicegateway.cli.base_cli import BaseCli


def _register_absent_extra(
    name: str, extra: str, target: typer.Typer | None = None
) -> None:
    """Stand a placeholder in for a command whose optional SDK is absent.

    Registering NOTHING would be worse. ``voicegw livekit`` would then be "no
    such command", which reads as the feature not existing rather than as one
    install away, and nothing in the output lets a reader tell those apart.

    Registered as a COMMAND rather than as a Typer group, which is the only
    shape that answers ``voicegw livekit agents``. A group resolves its
    subcommand strictly and fails with "No such command 'agents'", naming the
    wrong problem: the subcommand is real, the SDK behind it is not. A single
    command with ``allow_extra_args`` swallows the rest of the line and reports
    the actual cause. That was checked both ways round, because the group form
    looks correct and is not.

    Brackets are escaped for Rich. ``[{extra}]`` unescaped is parsed as a
    markup tag and silently deleted, which turned the install instruction into
    ``pip install 'voicegateway'``: still valid, still runnable, and it does not
    install the thing the message exists to ask for.
    """
    install = f"pip install 'voicegateway\\[{extra}]'"
    # ``target`` exists so a test can register onto a throwaway Typer instead of
    # mutating the process-wide ``app``, which other tests read.
    into = app if target is None else target

    @into.command(
        name=name,
        help=f"Unavailable here: needs `{install}`.",
        context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    )
    def _absent(ctx: typer.Context) -> None:
        console.print(
            f"[red]`voicegw {name}` needs the '{extra}' extra, which is not "
            f"installed.[/red]\nInstall it with: {install}"
        )
        raise typer.Exit(2)


# THE ONLY COMMAND GROUP THAT NEEDS A FRAMEWORK SDK, and therefore the only
# import here that is allowed to fail. It is last and guarded because an
# unguarded one took the whole CLI down: on a bare `pip install voicegateway`,
# `voicegw --help` raised ModuleNotFoundError: No module named 'livekit' from
# livekit_cli -> livekit_diag.admin, so a Pipecat or OpenRTC user could not
# reach `voicegw costs` (which needs no framework at all) without installing a
# framework they do not use. Every other cli module imports clean on a bare
# install; that was verified module by module, and this is the one exception.
try:
    from voicegateway.cli import livekit_cli as _livekit  # noqa: F401, E402
except ImportError:
    _register_absent_extra("livekit", "livekit")

__all__ = ["BaseCli", "app", "console"]
