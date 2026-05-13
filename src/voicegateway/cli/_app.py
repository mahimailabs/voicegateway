"""Shared Typer ``app`` and Rich ``console`` instances.

Internal module: every command submodule (``init``, ``status``, ...)
attaches itself to ``app`` via ``@app.command()`` at import time, and
prints through ``console`` for terminal output.

Splitting this off from ``__init__.py`` lets the package's
``__init__.py`` keep clean, isort-friendly import order: each
side-effect import of a command submodule resolves ``_app`` first,
which has no other dependencies inside the package, so there is no
circular-import surface.
"""

from __future__ import annotations

import typer
from rich.console import Console

app = typer.Typer(
    name="voicegw",
    help="VoiceGateway: cost tracking and reconciliation for LiveKit voice agents",
    no_args_is_help=True,
)
console = Console()


def _version_callback(value: bool) -> None:
    """Print the package version and exit (eager option)."""
    if value:
        from voicegateway import __version__

        console.print(__version__)
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the VoiceGateway version and exit.",
    ),
) -> None:
    """Root callback hosting global options like --version."""
