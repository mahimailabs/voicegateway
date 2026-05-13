"""Helpers for ``voicegateway.cli.brand``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import typer

from voicegateway.cli._app import console
from voicegateway.utils.cli._shared import _auth_headers


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
