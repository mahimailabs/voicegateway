"""``voicegw init`` command.

Carved out of voicegateway/cli/_legacy.py during the v0.1.0 section-2
refactor. Owns ``_PACKAGE_ROOT``, ``_EXAMPLE_CONFIG_CANDIDATES``, and
``_find_example_config`` because they are only used here.

Importing this module triggers the ``@app.command()`` decorator on
``init``, registering the command on the shared Typer ``app``.
``voicegateway/cli/__init__.py`` does this side-effect import.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import typer

# During the v0.1.0 migration ``app`` and ``console`` live in
# ``_legacy.py`` (they sit alongside the still-unmoved commands).
# Importing directly from the legacy module keeps ``__init__.py``'s
# import order safe under isort: the package can do
# ``from voicegateway.cli import init as _init`` before
# ``from voicegateway.cli._legacy import app, console`` and this
# submodule still resolves cleanly. When ``_legacy.py`` is finally
# emptied, ``app`` and ``console`` move to ``__init__.py`` and every
# submodule's import line flips to ``from voicegateway.cli import``.
from voicegateway.cli._legacy import app, console

# Resolve the repo root from this submodule's location:
#   voicegateway/cli/init.py -> voicegateway/cli -> voicegateway -> <root>
# The example configs live at the repo root in source checkouts and at
# the wheel's data root after install. ``resolve()`` makes the path
# stable under symlinks and editable installs.
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent

_EXAMPLE_CONFIG_CANDIDATES = [
    _PACKAGE_ROOT / "voicegw.example.yaml",
    _PACKAGE_ROOT / "gateway.example.yaml",
]


def _find_example_config() -> Path | None:
    for p in _EXAMPLE_CONFIG_CANDIDATES:
        if p.exists():
            return p
    return None


@app.command()
def init(
    output: str = typer.Option(
        "./voicegw.yaml", "--output", "-o", help="Output path for config file"
    ),
) -> None:
    """Create a voicegw.yaml configuration file."""
    dest = Path(output)
    if dest.exists():
        overwrite = typer.confirm(f"{dest} already exists. Overwrite?")
        if not overwrite:
            raise typer.Abort()

    example = _find_example_config()
    if example is not None:
        shutil.copy(example, dest)
    else:
        dest.write_text(
            "# VoiceGateway Configuration\n"
            "# See: https://github.com/mahimailabs/voicegateway\n\n"
            "providers: {}\nmodels:\n  stt: {}\n  llm: {}\n  tts: {}\n"
            "projects: {}\n"
        )

    console.print(f"[green]Created {dest}[/green]")
    console.print("Edit it with your API keys, models, and projects.")
