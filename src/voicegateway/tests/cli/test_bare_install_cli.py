"""The CLI must run on a bare ``pip install voicegateway``.

It did not. `voicegw --help` raised ``ModuleNotFoundError: No module named
'livekit'`` before printing anything, because ``cli/__init__`` imported every
command module eagerly and ``livekit_cli`` reaches ``livekit_diag.admin``, which
imports the LiveKit SDK at module scope. So a Pipecat or OpenRTC user could not
reach ``voicegw costs``, which needs no framework at all, without installing a
framework they do not use. On a tool whose whole claim is that it is
framework-agnostic, that is the claim failing at the first command.

Every other command module imports clean on a bare install. That was checked
module by module rather than assumed, and ``livekit_cli`` is the only exception,
which is why exactly one import here is guarded rather than all of them.

The subprocess test below is the one that would actually catch a regression. The
dev environment always has the LiveKit SDK installed, so nothing running
in-process can observe the failure: a test that cannot see the fault it guards
is not a guard. Blocking the import inside this process instead would mean
reloading ``voicegateway.cli`` against the process-wide ``app``, re-registering
every command onto it and corrupting the rest of the suite.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import typer
from typer.testing import CliRunner

from voicegateway.cli import _register_absent_extra

runner = CliRunner()


# --------------------------------------------------------------------------
# What the placeholder says
# --------------------------------------------------------------------------


def _stub() -> typer.Typer:
    """A throwaway app carrying the placeholder, so the real one is untouched."""
    into = typer.Typer()
    _register_absent_extra("livekit", "livekit", target=into)
    return into


def test_the_placeholder_names_the_extra_with_its_brackets_intact() -> None:
    """The bug this pins is a SILENT one: the message stayed grammatical.

    ``[livekit]`` unescaped is read as a Rich markup tag and deleted, so the
    instruction rendered as ``pip install 'voicegateway'``. Still valid, still
    runnable, and it installs precisely the thing that was already installed. A
    reader follows it, nothing changes, and the message that was supposed to fix
    them is the reason they are stuck.
    """
    result = runner.invoke(_stub(), ["livekit"])
    assert result.exit_code == 2
    assert "voicegateway[livekit]" in result.output


def test_the_placeholder_answers_a_subcommand_rather_than_rejecting_it() -> None:
    """``voicegw livekit agents`` must report the SDK, not the subcommand.

    Registered as a Typer group this fails with "No such command 'agents'",
    which names the wrong problem: the subcommand is real and the SDK behind it
    is not. Nobody types the bare group first; they type the command they want.
    """
    result = runner.invoke(_stub(), ["livekit", "agents", "--url", "x"])
    assert result.exit_code == 2
    assert "voicegateway[livekit]" in result.output


# --------------------------------------------------------------------------
# The regression itself, in a process where the SDK is genuinely unimportable
# --------------------------------------------------------------------------


_PROBE = textwrap.dedent(
    """
    import importlib.abc, sys

    class _Block(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "livekit" or fullname.startswith("livekit."):
                raise ImportError("blocked: simulating a bare install")
            return None

    sys.meta_path.insert(0, _Block())
    for name in [m for m in sys.modules if m.startswith("livekit")]:
        del sys.modules[name]

    import typer.main
    import voicegateway.cli as c

    # Built click app, not app.registered_commands: Typer leaves ``name`` None
    # when a command does not set one and derives it from the function at build
    # time, so reading the registration list reports ``costs`` as absent when it
    # is registered and working.
    names = set(typer.main.get_command(c.app).commands)
    print("IMPORTED_OK")
    print("HAS_LIVEKIT", "livekit" in names)
    print("HAS_COSTS", "costs" in names)
    """
)


def test_the_cli_imports_with_the_livekit_sdk_unimportable() -> None:
    """The actual regression, in a subprocess so the block is real.

    Asserts three things, and the third is the point of the whole change: the
    package imports at all, the placeholder took the ``livekit`` name so the
    command is not silently missing, and ``costs`` is reachable. ``costs`` is
    what a reader without LiveKit actually came for.
    """
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "IMPORTED_OK" in proc.stdout
    assert "HAS_LIVEKIT True" in proc.stdout
    assert "HAS_COSTS True" in proc.stdout
