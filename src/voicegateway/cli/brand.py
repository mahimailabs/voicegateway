"""``voicegw brand`` command group: scripted white-label provisioning."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import typer

from voicegateway.cli._app import app, console
from voicegateway.core.constants import DEFAULT_DASHBOARD_URL
from voicegateway.utils.cli._shared import _auth_headers
from voicegateway.utils.cli.brand import _post_branding, _upload_logo

brand_app = typer.Typer(
    name="brand",
    help="Scripted per-project white-label provisioning",
    no_args_is_help=True,
)
app.add_typer(brand_app, name="brand")


@brand_app.command("set")
def set_cmd(
    project: str = typer.Option(..., "--project", "-p", help="Project id to brand."),
    logo: Path | None = typer.Option(
        None, "--logo", help="Path to a PNG or SVG logo file."
    ),
    accent: str | None = typer.Option(
        None, "--accent", help='Hex color string (e.g. "#FF6633" or "#abc").'
    ),
    name: str | None = typer.Option(
        None, "--name", help="Product name shown in the dashboard chrome."
    ),
    dashboard_url: str = typer.Option(
        DEFAULT_DASHBOARD_URL,
        "--dashboard-url",
        help=f"Dashboard base URL (default {DEFAULT_DASHBOARD_URL}).",
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Print the resulting branding as JSON."
    ),
) -> None:
    """Set per-project branding via the dashboard API."""
    if not (logo or accent or name):
        console.print(
            "[red]At least one of --logo / --accent / --name is required.[/red]"
        )
        raise typer.Exit(2)

    base = dashboard_url.rstrip("/")
    with httpx.Client(base_url=base, timeout=30.0) as client:
        body: dict[str, Any] = {}
        if accent:
            body["accent_color"] = accent
        if name:
            body["product_name"] = name
        if logo:
            uploaded = _upload_logo(client, project, logo)
            body["logo_url"] = uploaded["logo_url"]

        result = _post_branding(client, project, body)

    if json_output:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return

    branding = result.get("branding") or {}
    console.print(f"[bold]Project {project}[/bold] branding updated:")
    console.print(f"  Logo:         {branding.get('logo_url') or '-'}")
    console.print(f"  Accent color: {branding.get('accent_color') or '-'}")
    console.print(f"  Product name: {branding.get('product_name') or '-'}")


@brand_app.command("clear")
def clear_cmd(
    project: str = typer.Option(..., "--project", "-p", help="Project id to clear."),
    dashboard_url: str = typer.Option(
        DEFAULT_DASHBOARD_URL,
        "--dashboard-url",
        help=f"Dashboard base URL (default {DEFAULT_DASHBOARD_URL}).",
    ),
) -> None:
    """Clear per-project branding (POSTs an empty payload)."""
    base = dashboard_url.rstrip("/")
    with httpx.Client(base_url=base, timeout=30.0) as client:
        _post_branding(client, project, {})
    console.print(f"[bold]Project {project}[/bold] branding cleared.")


@brand_app.command("show")
def show_cmd(
    project: str = typer.Option(..., "--project", "-p", help="Project id to inspect."),
    dashboard_url: str = typer.Option(
        DEFAULT_DASHBOARD_URL,
        "--dashboard-url",
        help=f"Dashboard base URL (default {DEFAULT_DASHBOARD_URL}).",
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Print the current branding as JSON."
    ),
) -> None:
    """Print the current branding for a project (read-only)."""
    base = dashboard_url.rstrip("/")
    with httpx.Client(base_url=base, timeout=30.0) as client:
        resp = client.get(f"/api/projects/{project}/branding", headers=_auth_headers())
    if resp.status_code == 404:
        console.print(f"[red]Project {project!r} not found.[/red]")
        raise typer.Exit(1)
    if resp.status_code >= 400:
        console.print(f"[red]Failed ({resp.status_code}): {resp.text}[/red]")
        raise typer.Exit(1)
    payload = resp.json()
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    branding = payload.get("branding") or {}
    if not branding:
        console.print(
            f"[bold]Project {project}[/bold]: no branding set (default brand)."
        )
        return
    console.print(f"[bold]Project {project}[/bold] branding:")
    console.print(f"  Logo:         {branding.get('logo_url') or '-'}")
    console.print(f"  Accent color: {branding.get('accent_color') or '-'}")
    console.print(f"  Product name: {branding.get('product_name') or '-'}")


__all__ = ["brand_app", "clear_cmd", "set_cmd", "show_cmd"]
