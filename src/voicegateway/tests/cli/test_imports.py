"""The CLI's public surface: the console-script entry point and its commands."""

from __future__ import annotations

from importlib.metadata import entry_points

from voicegateway.cli import app

# Command names users and scripts already depend on. Removing one is a
# breaking change and must be deliberate.
_STABLE_COMMAND_NAMES: frozenset[str] = frozenset(
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


def _registered_command_names() -> list[str]:
    names: list[str] = []
    for cmd in app.registered_commands:
        if cmd.name is not None:
            names.append(cmd.name)
        elif cmd.callback is not None:
            names.append(cmd.callback.__name__)
    return names


def test_voicegw_entry_point_resolves_to_app() -> None:
    """``voicegw = "voicegateway.cli:app"`` from pyproject.toml."""
    eps = [ep for ep in entry_points(group="console_scripts") if ep.name == "voicegw"]
    assert eps, "voicegw entry point not registered (is the package installed?)"
    [ep] = eps
    assert ep.value == "voicegateway.cli:app"
    assert ep.load() is app


def test_stable_commands_registered_once() -> None:
    names = _registered_command_names()
    missing = _STABLE_COMMAND_NAMES - set(names)
    assert not missing, f"command(s) no longer registered on app: {sorted(missing)}"
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, f"duplicate command name(s) on app: {dupes}"
