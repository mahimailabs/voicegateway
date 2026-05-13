"""``voicegw init`` command.

Carved out of voicegateway/cli/_legacy.py during the v0.1.0 section-2
refactor. The example config that init copies lives at
``voicegateway/data/voicegw.example.yaml`` so the wheel always ships
it and the repo root stays uncluttered.

Importing this module triggers the ``@app.command()`` decorator on
``init``, registering the command on the shared Typer ``app``.
``voicegateway/cli/__init__.py`` does this side-effect import.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import typer

from voicegateway.cli._app import app, console


def _read_example_config() -> str:
    """Return the canonical example config shipped with the wheel."""
    return (
        resources.files("voicegateway.data")
        .joinpath("voicegw.example.yaml")
        .read_text(encoding="utf-8")
    )


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

    dest.write_text(_read_example_config(), encoding="utf-8")
    console.print(f"[green]Created {dest}[/green]")
    console.print("Edit it with your API keys, models, and projects.")
