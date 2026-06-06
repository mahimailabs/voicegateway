"""``voicegw init`` command."""

from __future__ import annotations

from pathlib import Path

import typer

from voicegateway.cli._app import app
from voicegateway.cli.base_cli import BaseCli
from voicegateway.utils.cli.init import _read_example_config, _read_minimal_config

_cli = BaseCli()


@app.command()
def init(
    output: str = typer.Option(
        "./voicegw.yaml", "--output", "-o", help="Output path for config file"
    ),
    full: bool = typer.Option(
        False,
        "--full",
        help="Write the full annotated reference config instead of the minimal starter.",
    ),
) -> None:
    """Create a voicegw.yaml configuration file."""
    dest = Path(output)
    if dest.exists():
        overwrite = typer.confirm(f"{dest} already exists. Overwrite?")
        if not overwrite:
            raise typer.Abort()

    dest.write_text(
        _read_example_config() if full else _read_minimal_config(), encoding="utf-8"
    )
    _cli.success(f"Created {dest}")
    _cli.console.print("Edit it with your API keys, models, and projects.")
    if not full:
        _cli.console.print("For every option, run: voicegw init --full")
