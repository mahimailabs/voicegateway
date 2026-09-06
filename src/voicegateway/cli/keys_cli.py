"""``voicegw keys`` command group: mint, list, audit and revoke virtual keys.

Until 0.26.0 the only way to mint a key was the HTTP API, which meant the
first key had to be created by a server that was not yet protected by one.
More to the point for VG-SEC-006: an operator asked to re-mint their wildcard
keys with explicit scopes needs a way to see which keys those are and a way
to make the replacements. ``keys audit`` and ``keys create --scopes`` are
that pair.
"""

from __future__ import annotations

import typer
from rich.table import Table

from voicegateway.cli._app import app, console
from voicegateway.cli.base_cli import BaseCli
from voicegateway.core import scopes as scopes_mod
from voicegateway.repository import api_keys_repository as api_keys

_cli = BaseCli()

keys_app = typer.Typer(
    name="keys",
    help="Mint, list, audit and revoke virtual API keys.",
    no_args_is_help=True,
)
app.add_typer(keys_app, name="keys")

_SCOPES_HELP = "Comma-separated: " + ", ".join(sorted(scopes_mod.MINTABLE))


def _storage(config: str | None):
    """Load the Gateway and return its storage, failing the CLI way."""
    return _cli.require_storage(_cli.require_gateway(config))


@keys_app.command("create")
def create_cmd(
    name: str = typer.Option(..., "--name", "-n", help="Human label for the key."),
    scopes: str = typer.Option(..., "--scopes", "-s", help=_SCOPES_HELP),
    tenant: str = typer.Option(
        None, "--tenant", "-t", help="Bind the key to one tenant."
    ),
    role: str = typer.Option("tenant", "--role", help="'tenant' or 'admin'."),
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
) -> None:
    """Mint a key. The plaintext is printed once and never stored."""
    storage = _storage(config)

    async def _run():
        async with storage.session() as db:
            return await api_keys.create_api_key(
                db, name=name, scopes=scopes, tenant_id=tenant, role=role
            )

    try:
        created = _cli.async_run(_run())
    except ValueError as exc:
        _cli.fail(str(exc))
    console.print(f"[green]Created key {created.id} ({name!r}).[/green]")
    console.print(f"[bold]{created.plaintext}[/bold]")
    _cli.warn("This is the only time the plaintext is shown. Store it now.")


@keys_app.command("list")
def list_cmd(
    include_revoked: bool = typer.Option(
        False, "--include-revoked", help="Also show revoked keys."
    ),
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
) -> None:
    """List every key. The plaintext and the hash are never shown."""
    storage = _storage(config)

    async def _run():
        async with storage.session() as db:
            return await api_keys.list_keys(db, include_revoked=include_revoked)

    rows = _cli.async_run(_run())
    if not rows:
        console.print("[yellow]No keys.[/yellow]")
        return
    table = Table(title="API keys")
    for column in ("ID", "Name", "Prefix", "Tenant", "Issued", "Revoked"):
        table.add_column(column)
    for row in rows:
        table.add_row(
            str(row.id),
            row.name,
            row.key_prefix,
            row.tenant_id or "-",
            row.issued_at,
            row.revoked_at or "-",
        )
    console.print(table)


@keys_app.command("audit")
def audit_cmd(
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
) -> None:
    """List keys still carrying the wildcard scope. Re-mint them before 0.27.0.

    Exits non-zero when any are found, so this is usable as a deployment
    gate rather than only as something to read.
    """
    storage = _storage(config)

    async def _run():
        async with storage.session() as db:
            return await api_keys.list_wildcard_keys(db)

    rows = _cli.async_run(_run())
    if not rows:
        console.print("[green]No wildcard keys.[/green]")
        return
    _cli.warn(
        f"{len(rows)} key(s) still carry the wildcard scope. They authorize "
        "everything today and will authorize nothing in 0.27.0."
    )
    table = Table(title="Wildcard keys")
    for column in ("ID", "Name", "Tenant", "Role", "Scopes"):
        table.add_column(column)
    for row in rows:
        table.add_row(str(row.id), row.name, row.tenant_id or "-", row.role, row.scopes)
    console.print(table)
    console.print(
        "\nRe-mint each with: "
        "[bold]voicegw keys create --name <name> --scopes <scopes>[/bold]\n"
        f"Scopes: {_SCOPES_HELP}"
    )
    raise typer.Exit(1)


@keys_app.command("revoke")
def revoke_cmd(
    key_id: int = typer.Argument(..., help="Id of the key to revoke."),
    config: str = typer.Option(None, "--config", "-c", help="Path to voicegw.yaml"),
) -> None:
    """Soft-revoke a key. The row is kept so the audit trail survives."""
    storage = _storage(config)

    async def _run():
        async with storage.session() as db:
            return await api_keys.revoke(db, key_id)

    if _cli.async_run(_run()):
        console.print(f"[green]Revoked key {key_id}.[/green]")
    else:
        _cli.fail(f"Key {key_id} not found, or already revoked.")
