"""``voicegw rotate-secret`` command."""

from __future__ import annotations

import os

import typer

from voicegateway.cli._app import app, console
from voicegateway.cli.base_cli import BaseCli

_cli = BaseCli()


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
        _cli.fail(
            "VOICEGW_SECRET is not set. Set it to the new primary "
            "key (the value you want to encrypt under going forward), then "
            "re-run."
        )
    if not fallback:
        _cli.fail(
            "VOICEGW_SECRET_FALLBACK is not set. Set it to the previous "
            "VOICEGW_SECRET so the rotation can decrypt the existing rows, "
            "then re-run."
        )

    gw = _cli.require_gateway(config)
    if gw.storage is None:
        _cli.warn(
            "Cost tracking is disabled in voicegw.yaml; there are no "
            "managed_providers rows to rotate."
        )
        raise typer.Exit(0)

    rows = _cli.async_run(gw.storage.list_managed_providers())
    if not rows:
        console.print("No managed_providers rows to rotate.")
        raise typer.Exit(0)

    console.print(
        f"[bold]Rotating {len(rows)} managed credential(s)[/bold] under "
        "the new VOICEGW_SECRET."
    )
    if not yes and not typer.confirm("Proceed?"):
        raise typer.Abort()

    summary = _cli.async_run(gw.storage.rotate_managed_credentials())
    _cli.success(
        f"Rotated {summary['rotated']} row(s). "
        f"Skipped {summary['skipped_empty']} empty row(s)."
    )
    if summary["failed"]:
        _cli.error(
            "Failed to rotate the following rows (no configured key decrypts them):"
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
