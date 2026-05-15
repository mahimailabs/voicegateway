"""Shared CLI surface: gateway loading, styled output, exit helpers.

Every command module repeats four patterns: load the gateway and exit
on error, check ``gw.storage is None``, print Rich-styled error or
status messages, wrap async calls in ``asyncio.run``. :class:`BaseCli`
is the single home for that surface.

Modules use this class by **composition**: instantiate it once at module
top (``_cli = BaseCli()``) and call methods directly inside command
bodies. Subclassing is supported but not required; Typer is function-
first by design and command bodies stay functions.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, NoReturn

import typer
from rich.console import Console

from voicegateway.cli._app import console as _shared_console

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from voicegateway.core.gateway import Gateway


class BaseCli:
    """Helpers every command module reuses.

    The intended usage is::

        _cli = BaseCli()

        @app.command()
        def status() -> None:
            gw = _cli.require_gateway(None)
            storage = _cli.require_storage(gw)
            ...

    Subclassing is fine when a multi-command group wants to bundle
    state, but composition is the default.
    """

    def __init__(self, console: Console | None = None) -> None:
        self.console: Console = console or _shared_console

    # --- Styled output ----------------------------------------------------

    def error(self, msg: str) -> None:
        """Print ``msg`` in red. Does not exit."""
        self.console.print(f"[red]{msg}[/red]")

    def success(self, msg: str) -> None:
        """Print ``msg`` in green."""
        self.console.print(f"[green]{msg}[/green]")

    def warn(self, msg: str) -> None:
        """Print ``msg`` in yellow."""
        self.console.print(f"[yellow]{msg}[/yellow]")

    def info(self, msg: str) -> None:
        """Print ``msg`` in cyan."""
        self.console.print(f"[cyan]{msg}[/cyan]")

    def dim(self, msg: str) -> None:
        """Print ``msg`` dimmed."""
        self.console.print(f"[dim]{msg}[/dim]")

    # --- Exit helpers -----------------------------------------------------

    def fail(self, msg: str, code: int = 1) -> NoReturn:
        """Print ``msg`` in red and exit with ``code``."""
        self.error(msg)
        raise typer.Exit(code)

    # --- Gateway / storage / async ---------------------------------------

    def require_gateway(self, config_path: str | None) -> Gateway:
        """Load a Gateway; fail with a red message on any exception."""
        from voicegateway.core.gateway import Gateway

        try:
            return Gateway(config_path=config_path)
        except Exception as exc:
            self.fail(f"Error loading config: {exc}")

    def require_storage(self, gw: Gateway) -> Any:
        """Return ``gw.storage`` or fail with the canonical message."""
        if gw.storage is None:
            self.fail("Storage backend not configured (cost_tracking.db_path).")
        return gw.storage

    @staticmethod
    def async_run(coro: Coroutine[Any, Any, Any]) -> Any:
        """Thin wrapper around ``asyncio.run`` for symmetry with the helpers above."""
        return asyncio.run(coro)


__all__ = ["BaseCli"]
