"""Backward-compat gate for the v0.0.5 → v0.1.0 cli refactor.

The v0.0.5 cli was a single ``voicegateway/cli.py`` module exporting
the Typer ``app`` (and incidentally the Rich ``console``). Any
external script that imported ``app`` to drive Typer's testing
``CliRunner``, or anything pinned to the
``voicegw = "voicegateway.cli:app"`` console-script entry point,
must still resolve cleanly under v0.1.0.

These tests are the regression gate. If a future refactor shadows
``app`` with something that is not a Typer instance, drops a v0.0.5
command, renames one to a non-canonical alias, or otherwise breaks
the import contract, this file fails immediately.
"""

from __future__ import annotations

from importlib.metadata import entry_points

import typer

# These imports themselves are part of the contract. If a future
# refactor moves ``app`` or ``console`` somewhere ``from
# voicegateway.cli import app`` cannot reach, the test module
# fails to load and pytest reports it.
from voicegateway.cli import app, console

# ---------------------------------------------------------------------------
# Direct import paths.
# ---------------------------------------------------------------------------


def test_app_is_a_typer_instance() -> None:
    """``app`` is the Typer object every command registers on.

    External scripts use ``typer.testing.CliRunner`` against this
    object; a regression where ``app`` becomes anything else (a
    Click ``Group``, a function, ``None``) breaks every such caller.
    """
    assert isinstance(app, typer.Typer)
    assert app.info.name == "voicegw"


def test_console_is_a_rich_console() -> None:
    """``console`` keeps its v0.0.5 type.

    Some external integrations (notebooks, embedded shells) pull the
    package's ``console`` to share a Rich rendering context. Loosely
    typed but the duck shape we promise is ``rich.console.Console``.
    """
    from rich.console import Console

    assert isinstance(console, Console)


def test_dotted_attribute_access_still_works() -> None:
    """``import voicegateway.cli; voicegateway.cli.app`` resolves.

    Some callers prefer attribute access over ``from … import …``;
    the contract covers both.
    """
    import voicegateway.cli as cli_pkg

    assert cli_pkg.app is app
    assert cli_pkg.console is console


# ---------------------------------------------------------------------------
# Console-script entry point.
# ---------------------------------------------------------------------------


def test_voicegw_entry_point_resolves_to_app() -> None:
    """``voicegw = "voicegateway.cli:app"`` from pyproject.toml.

    A regression where the entry-point target moves (e.g. someone
    rewires the pointer to ``voicegateway.cli._app:app`` or to a
    different module) breaks the installed CLI command without
    breaking any direct imports. The entry-points registry is the
    only place that catches it.
    """
    eps = entry_points()
    console_scripts = eps.select(group="console_scripts")
    voicegw_eps = [ep for ep in console_scripts if ep.name == "voicegw"]
    assert voicegw_eps, "voicegw entry point not registered (is the package installed?)"
    [ep] = voicegw_eps
    # The entry-point string is "voicegateway.cli:app" verbatim. Any
    # future change here forces a deliberate update of pyproject.toml
    # plus this assertion, which is the point.
    assert ep.value == "voicegateway.cli:app"
    assert ep.load() is app


# ---------------------------------------------------------------------------
# Every v0.0.5 command name still registered.
# ---------------------------------------------------------------------------


_V005_COMMAND_NAMES: frozenset[str] = frozenset(
    {
        "init",
        "rotate-secret",
        "status",
        "costs",
        "projects",
        "project",
        "logs",
        "smoke-test",
        "serve",
        "dashboard",
        "export-costs",
        "reconcile",
        "mcp",
    }
)

# Commands the v0.1.0 release adds on top of the v0.0.5 surface.
# Each new command lands its own deliberate update here, gating the
# command-count assertion below. When v0.1.0 ships, the union of
# these two frozensets is the documented end-state surface.
_V010_COMMAND_NAMES: frozenset[str] = frozenset(
    {
        "onboard",
        "start",
        "stop",
        "restart",
    }
)


def _registered_command_names() -> set[str]:
    """Names every Typer command registered on ``app`` answers to."""
    names: set[str] = set()
    for cmd in app.registered_commands:
        if cmd.name is not None:
            names.add(cmd.name)
        elif cmd.callback is not None:
            names.add(cmd.callback.__name__)
    return names


def test_every_v005_command_still_registered() -> None:
    """Every v0.0.5 command name resolves on the v0.1.0 app.

    A regression where a section-2 carve-out drops or renames one of
    these (say, ``smoke-test`` becomes ``smoketest``) trips this gate
    without depending on anyone running the actual command.
    """
    registered = _registered_command_names()
    missing = _V005_COMMAND_NAMES - registered
    assert not missing, (
        f"v0.0.5 command(s) no longer registered on app: {sorted(missing)}. "
        f"Registered: {sorted(registered)}."
    )


def test_no_command_name_collisions() -> None:
    """Each command name appears exactly once.

    Double-registration would mean two submodules attached the same
    ``@app.command(name=...)`` decorator and one silently shadows
    the other under Typer.
    """
    names: list[str] = []
    for cmd in app.registered_commands:
        if cmd.name is not None:
            names.append(cmd.name)
        elif cmd.callback is not None:
            names.append(cmd.callback.__name__)
    assert len(names) == len(set(names)), (
        f"Duplicate command name(s) on app: {sorted({n for n in names if names.count(n) > 1})}."
    )


def test_command_count_matches_documented_surface() -> None:
    """Exactly the documented (v0.0.5 + v0.1.0-additions) count.

    Each new v0.1.0 command (``onboard`` lands first; ``doctor``,
    ``migrate``, the lifecycle group follow) bumps
    ``_V010_COMMAND_NAMES`` above. A surprise command — one that
    registers on app without showing up in either frozenset — trips
    this gate, which is exactly the deliberate touch-point we want
    for "the public command surface grew."
    """
    expected = _V005_COMMAND_NAMES | _V010_COMMAND_NAMES
    registered = _registered_command_names()
    assert registered == expected, (
        f"Registered commands diverge from documented surface. "
        f"Extra: {sorted(registered - expected)}. "
        f"Missing: {sorted(expected - registered)}."
    )
