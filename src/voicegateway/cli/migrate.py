"""``voicegw migrate`` command."""

from __future__ import annotations

from pathlib import Path

import typer

from voicegateway.cli._app import app
from voicegateway.utils.cli._shared import _config_home
from voicegateway.utils.cli.migrate import _build_report, _render


@app.command()
def migrate(
    config_home: str = typer.Option(
        None,
        "--config-home",
        help="Override the canonical config home (default: ~/.config/voicegateway).",
    ),
) -> None:
    """Migrate a v0.0.5 install into the v0.1.0 layout."""
    home = Path(config_home) if config_home else _config_home()
    report = _build_report(home)
    _render(report)
