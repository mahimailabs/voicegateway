"""``voicegw brand`` command group: scripted white-label provisioning.

Implements the operator-side companion to the v0.5.0 dashboard's
BrandingModal (T18). Hits the same dashboard endpoints as the FE so
provisioning scripts can roll new agencies without clicking through
the UI.

Subcommand:

* ``voicegw brand set --project <id> [--logo <path>] [--accent <hex>]
  [--name <str>] [--dashboard-url URL]`` — at least one of --logo /
  --accent / --name is required. The CLI POSTs each surface to the
  matching endpoint and prints the resulting branding payload.

``voicegw brand clear --project <id>`` clears the branding (POSTs an
empty payload). Note that the v0.5.0 backend's COALESCE-protected
UPSERT means the clear may not fully blank stored values until a
dedicated DELETE endpoint ships (see T18 journal note).

The CLI uses ``httpx`` (already a base dep) and reads the
``VOICEGW_API_KEY`` env var for the Bearer header when set. Default
dashboard URL is ``http://127.0.0.1:9090`` matching the
``voicegw dashboard`` defaults.

Path correction note: Foundry text listed ``voicegw/cli/brand.py``;
the actual CLI subpackage is ``voicegateway/cli/``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import typer

from voicegateway.cli._app import app, console

brand_app = typer.Typer(
    name="brand",
    help="Scripted per-project white-label provisioning (REQ-VG-ROUTE-004).",
    no_args_is_help=True,
)
app.add_typer(brand_app, name="brand")


_DEFAULT_DASHBOARD_URL = "http://127.0.0.1:9090"


def _auth_headers() -> dict[str, str]:
    """Return Bearer auth headers when ``VOICEGW_API_KEY`` is set."""
    token = os.environ.get("VOICEGW_API_KEY", "").strip()
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _upload_logo(
    client: httpx.Client, project_id: str, logo_path: Path
) -> dict[str, Any]:
    if not logo_path.is_file():
        console.print(f"[red]Logo file not found: {logo_path}[/red]")
        raise typer.Exit(2)
    suffix = logo_path.suffix.lower()
    if suffix == ".png":
        content_type = "image/png"
    elif suffix == ".svg":
        content_type = "image/svg+xml"
    else:
        console.print(
            f"[red]Unsupported logo extension {suffix!r}; must be .png or .svg[/red]"
        )
        raise typer.Exit(2)

    with open(logo_path, "rb") as fh:
        resp = client.post(
            f"/api/projects/{project_id}/branding/logo",
            files={"file": (logo_path.name, fh, content_type)},
            headers=_auth_headers(),
        )
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", "")
        except json.JSONDecodeError:
            detail = resp.text
        console.print(f"[red]Logo upload failed ({resp.status_code}): {detail}[/red]")
        raise typer.Exit(1)
    return dict(resp.json())


def _post_branding(
    client: httpx.Client, project_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json", **_auth_headers()}
    resp = client.post(
        f"/api/projects/{project_id}/branding",
        content=json.dumps(body),
        headers=headers,
    )
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", "")
        except json.JSONDecodeError:
            detail = resp.text
        console.print(
            f"[red]Branding update failed ({resp.status_code}): {detail}[/red]"
        )
        raise typer.Exit(1)
    return dict(resp.json())


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
        _DEFAULT_DASHBOARD_URL,
        "--dashboard-url",
        help=f"Dashboard base URL (default {_DEFAULT_DASHBOARD_URL}).",
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Print the resulting branding as JSON."
    ),
) -> None:
    """Set per-project branding via the dashboard API.

    At least one of --logo / --accent / --name is required. The CLI
    uploads the logo first (if provided), then POSTs the merged
    branding so the resulting payload carries logo_url + the
    user-supplied accent_color / product_name.
    """
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
        _DEFAULT_DASHBOARD_URL,
        "--dashboard-url",
        help=f"Dashboard base URL (default {_DEFAULT_DASHBOARD_URL}).",
    ),
) -> None:
    """Clear per-project branding (POSTs an empty payload).

    NOTE: the v0.5.0 backend's COALESCE-protected UPSERT means this
    may not fully blank already-stored values until a dedicated
    DELETE endpoint ships. Documented in the v0.5.0/T18 journal.
    """
    base = dashboard_url.rstrip("/")
    with httpx.Client(base_url=base, timeout=30.0) as client:
        _post_branding(client, project, {})
    console.print(f"[bold]Project {project}[/bold] branding cleared.")


@brand_app.command("show")
def show_cmd(
    project: str = typer.Option(..., "--project", "-p", help="Project id to inspect."),
    dashboard_url: str = typer.Option(
        _DEFAULT_DASHBOARD_URL,
        "--dashboard-url",
        help=f"Dashboard base URL (default {_DEFAULT_DASHBOARD_URL}).",
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
