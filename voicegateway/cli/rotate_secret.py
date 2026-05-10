"""``voicegw rotate-secret`` command.

Carved out of voicegateway/cli/_legacy.py during the v0.1.0 section-2
refactor. Re-encrypts every row in ``managed_providers`` under a new
primary Fernet key while still being able to decrypt under the
previous one (held in ``VOICEGW_SECRET_FALLBACK``).
"""

from __future__ import annotations

import asyncio
import os

import typer

# See voicegateway/cli/init.py for the rationale on importing
# ``app`` and ``console`` from ``_legacy`` rather than from the
# package during the v0.1.0 migration period.
from voicegateway.cli._legacy import _load_gateway, app, console


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
    """Re-encrypt managed provider keys under the new primary Fernet key.

    Workflow:

      1. Generate a fresh key:  python -c 'from cryptography.fernet
         import Fernet; print(Fernet.generate_key().decode())'
      2. Set VOICEGW_SECRET to the new value.
      3. Set VOICEGW_SECRET_FALLBACK to the previous VOICEGW_SECRET.
      4. Run `voicegw rotate-secret`.
      5. Once it completes successfully, remove
         VOICEGW_SECRET_FALLBACK from the environment.

    The command refuses to run unless both env vars are present,
    so an accidental invocation cannot truncate access to the
    fallback key.
    """
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
