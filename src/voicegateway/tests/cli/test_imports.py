"""Backward-compat gate for the v0.0.5 → v0.1.0 cli refactor."""

from __future__ import annotations

import subprocess
import sys
from importlib.metadata import entry_points

import typer

# These imports themselves are part of the contract. If a future
# refactor moves ``app`` or ``console`` somewhere ``from
# voicegateway.cli import app`` cannot reach, the test module
# fails to load and pytest reports it.
from voicegateway.cli import app, console


def test_voicegateway_cli_import_does_not_load_textual() -> None:
    """``from voicegateway.cli import app`` must not pull in ``textual``."""
    probe = (
        "import sys, voicegateway.cli;\n"
        "leaked = sorted(m for m in sys.modules if m == 'textual' "
        "or m.startswith('textual.'));\n"
        "assert not leaked, f'textual leaked into the CLI import "
        "chain: {leaked!r}'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"voicegateway.cli import pulled in textual.\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# Direct import paths.
# ---------------------------------------------------------------------------


def test_app_is_a_typer_instance() -> None:
    """``app`` is the Typer object every command registers on."""
    assert isinstance(app, typer.Typer)
    assert app.info.name == "voicegw"


def test_console_is_a_rich_console() -> None:
    """``console`` keeps its v0.0.5 type."""
    from rich.console import Console

    assert isinstance(console, Console)


def test_dotted_attribute_access_still_works() -> None:
    """``import voicegateway.cli; voicegateway.cli.app`` resolves."""
    import voicegateway.cli as cli_pkg

    assert cli_pkg.app is app
    assert cli_pkg.console is console


# ---------------------------------------------------------------------------
# Console-script entry point.
# ---------------------------------------------------------------------------


def test_voicegw_entry_point_resolves_to_app() -> None:
    """``voicegw = "voicegateway.cli:app"`` from pyproject.toml."""
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
        "daemon-logs",
        "uninstall-daemon",
        "doctor",
        "migrate",
    }
)

# Commands the v0.1.1 release adds on top of v0.1.0. Same gating
# pattern: a deliberate touch-point for "the public command surface
# grew." When v0.1.1 ships, the union of the three frozensets is the
# documented end-state surface.
_V011_COMMAND_NAMES: frozenset[str] = frozenset({"tui"})

# Commands the v0.3.0 release adds (replay signpost, T15).
_V030_COMMAND_NAMES: frozenset[str] = frozenset({"replay"})

# The framework-agnostic onboard/check reframe adds `check` (the `smoke-test`
# name lives on as a hidden alias, so it stays in the _V005 set above).
_FRAMEWORK_AGNOSTIC_COMMAND_NAMES: frozenset[str] = frozenset({"check"})


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
    """Every v0.0.5 command name resolves on the v0.1.0 app."""
    registered = _registered_command_names()
    missing = _V005_COMMAND_NAMES - registered
    assert not missing, (
        f"v0.0.5 command(s) no longer registered on app: {sorted(missing)}. "
        f"Registered: {sorted(registered)}."
    )


def test_no_command_name_collisions() -> None:
    """Each command name appears exactly once."""
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
    """Exactly the documented (v0.0.5 + v0.1.0-additions) count."""
    expected = (
        _V005_COMMAND_NAMES
        | _V010_COMMAND_NAMES
        | _V011_COMMAND_NAMES
        | _V030_COMMAND_NAMES
        | _FRAMEWORK_AGNOSTIC_COMMAND_NAMES
    )
    registered = _registered_command_names()
    assert registered == expected, (
        f"Registered commands diverge from documented surface. "
        f"Extra: {sorted(registered - expected)}. "
        f"Missing: {sorted(expected - registered)}."
    )
