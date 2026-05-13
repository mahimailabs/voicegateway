"""``voicegw tenant`` command group: read-only tenant index for CI scripts."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import typer

from voicegateway.cli._app import app, console
from voicegateway.cli._helpers import _load_gateway

tenant_app = typer.Typer(
    name="tenant",
    help="Read-only tenant index for CI scripts. Use the dashboard to issue keys.",
    no_args_is_help=True,
)
app.add_typer(tenant_app, name="tenant")


def _format_relative(iso: str | None) -> str:
    """Return ``iso`` verbatim or ``-`` when ``None``."""
    return iso if iso else "-"


async def _list_tenants_async(
    storage: Any, *, limit: int, query: str | None
) -> tuple[list[Any], Any]:
    """Run ``tenants_repo.list_tenants`` + the unattributed aggregator."""
    from voicegateway.storage import tenants_repo

    db = await storage._ensure_initialized()
    rows = await tenants_repo.list_tenants(db, limit=limit, query=query)
    unattributed = await tenants_repo.get_unattributed_aggregates(db)
    return rows, unattributed


async def _get_tenant_async(storage: Any, tenant_id: str) -> Any:
    from voicegateway.storage import tenants_repo

    db = await storage._ensure_initialized()
    return await tenants_repo.get_tenant(db, tenant_id)


@tenant_app.command("list")
def list_cmd(
    config: str | None = typer.Option(
        None, "--config", "-c", help="Path to voicegw.yaml."
    ),
    limit: int = typer.Option(
        50, "--limit", "-n", min=1, max=1000, help="Max tenants to list."
    ),
    query: str | None = typer.Option(
        None, "--query", "-q", help="Substring filter on tenant_id."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Print one JSON object per call instead of a table."
    ),
) -> None:
    """List tenants ordered by most-recently-active."""
    gw = _load_gateway(config)
    if gw.storage is None:
        console.print(
            "[red]Storage backend not configured (cost_tracking.db_path).[/red]"
        )
        raise typer.Exit(1)

    rows, unattributed = asyncio.run(
        _list_tenants_async(gw.storage, limit=limit, query=query),
    )

    if json_output:
        payload = {
            "tenants": [
                {
                    "tenant_id": r.tenant_id,
                    "session_count": r.session_count,
                    "total_cost_usd": r.total_cost_usd,
                    "first_seen": r.first_seen,
                    "last_seen": r.last_seen,
                }
                for r in rows
            ],
            "unattributed": {
                "session_count": unattributed.session_count,
                "total_cost_usd": unattributed.total_cost_usd,
                "first_seen": unattributed.first_seen,
                "last_seen": unattributed.last_seen,
            },
        }
        # ensure_ascii=False so tenant names with unicode (the OQ2
        # cap is 128-char UTF-8) round-trip cleanly.
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    if not rows and unattributed.session_count == 0:
        console.print("[dim]No tenants seen yet.[/dim]")
        return

    console.print("[bold]Tenants[/bold]")
    if rows:
        console.print(
            f"{'TENANT':<32} {'SESSIONS':>10} {'COST USD':>12} {'LAST SEEN':<28}"
        )
        console.print("-" * 84)
        for r in rows:
            tenant_display = (
                r.tenant_id if len(r.tenant_id) <= 31 else r.tenant_id[:28] + "..."
            )
            console.print(
                f"{tenant_display:<32} {r.session_count:>10} "
                f"{r.total_cost_usd:>12.4f} {_format_relative(r.last_seen):<28}"
            )
    else:
        console.print("[dim](no attributed tenants in the filtered window)[/dim]")

    if unattributed.session_count > 0:
        console.print(
            f"\n[dim]Unattributed: {unattributed.session_count} session(s), "
            f"${unattributed.total_cost_usd:.4f} total, "
            f"last seen {_format_relative(unattributed.last_seen)}.[/dim]"
        )


@tenant_app.command("show")
def show_cmd(
    tenant_id: str = typer.Argument(..., help="Tenant id to look up."),
    config: str | None = typer.Option(
        None, "--config", "-c", help="Path to voicegw.yaml."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Print a JSON object instead of a key-value list."
    ),
) -> None:
    """Print aggregates for a single tenant. Exits 1 when unseen."""
    if not tenant_id.strip():
        console.print("[red]tenant_id is required.[/red]")
        raise typer.Exit(2)

    gw = _load_gateway(config)
    if gw.storage is None:
        console.print(
            "[red]Storage backend not configured (cost_tracking.db_path).[/red]"
        )
        raise typer.Exit(1)

    row = asyncio.run(_get_tenant_async(gw.storage, tenant_id))
    if row is None:
        console.print(f"[red]Tenant {tenant_id!r} has no sessions.[/red]")
        raise typer.Exit(1)

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "tenant_id": row.tenant_id,
                    "session_count": row.session_count,
                    "total_cost_usd": row.total_cost_usd,
                    "first_seen": row.first_seen,
                    "last_seen": row.last_seen,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    console.print(f"[bold]Tenant {row.tenant_id}[/bold]")
    console.print(f"  Sessions:   {row.session_count}")
    console.print(f"  Total cost: ${row.total_cost_usd:.4f}")
    console.print(f"  First seen: {_format_relative(row.first_seen)}")
    console.print(f"  Last seen:  {_format_relative(row.last_seen)}")


__all__ = ["list_cmd", "show_cmd", "tenant_app"]
