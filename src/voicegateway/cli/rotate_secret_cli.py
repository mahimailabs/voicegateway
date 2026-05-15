"""``voicegw rotate-secret`` command."""

from __future__ import annotations

import asyncio
import os

import typer

from voicegateway.cli._app import app, console
from voicegateway.utils.cli._shared import _load_gateway


@app.command(name="rotate-secret")
def rotate_secret(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip the confirmation prompt.",
    ),
) -> None:
    """Re-encrypt managed provider keys under the new primary Fernet key."""
    new_primary = os.environ.get("VOICEGW_SECRET")
    fallback = os.environ.get("VOICEGW_SECRET_FALLBACK")
    if not new_primary:
        console.print(
            "[red]VOICEGW_SECRET is not set.[/red] Set it to the new primary "
            "key (the value you want to encrypt under going forward), then "
            "re-run."
        )
        raise typer.Exit(1)
    if not fallback:
        console.print(
            "[red]VOICEGW_SECRET_FALLBACK is not set.[/red] Set it to the "
            "previous VOICEGW_SECRET so the rotation can decrypt the existing "
            "rows, then re-run."
        )
        raise typer.Exit(1)

    gw = _load_gateway(config)
    if gw.storage is None:
        console.print(
            "[yellow]Cost tracking is disabled in voicegw.yaml; there are no "
            "managed_providers rows to rotate.[/yellow]"
        )
        raise typer.Exit(0)

    rows = asyncio.run(gw.storage.list_managed_providers())
    if not rows:
        console.print("No managed_providers rows to rotate.")
        raise typer.Exit(0)

    console.print(
        f"[bold]Rotating {len(rows)} managed credential(s)[/bold] under "
        "the new VOICEGW_SECRET."
    )
    if not yes and not typer.confirm("Proceed?"):
        raise typer.Abort()

    summary = asyncio.run(gw.storage.rotate_managed_credentials())
    console.print(
        f"[green]Rotated {summary['rotated']} row(s).[/green] "
        f"Skipped {summary['skipped_empty']} empty row(s)."
    )
    if summary["failed"]:
        console.print(
            "[red]Failed to rotate the following rows[/red] "
            "(no configured key decrypts them):"
        )
        for pid in summary["failed"]:
            console.print(f"  - {pid}")
        console.print(
            "Re-add the affected providers via the dashboard or "
            "`vg_add_provider` (MCP) once the rotation finishes."
        )
        raise typer.Exit(2)

    console.print(
        "[bold]Now remove VOICEGW_SECRET_FALLBACK from the environment.[/bold] "
        "Leaving it set keeps the previous key acceptable for decryption, "
        "weakening the rotation."
    )
