"""Helpers for ``voicegateway.cli.guardrails``."""

from __future__ import annotations

import json
from typing import Any

import httpx
import typer

from voicegateway.cli._app import console
from voicegateway.utils.cli._shared import _auth_headers


def _request(
    method: str,
    path: str,
    *,
    dashboard_url: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json", **_auth_headers()}
    with httpx.Client(base_url=dashboard_url.rstrip("/"), timeout=30.0) as client:
        resp = client.request(
            method,
            path,
            content=json.dumps(body) if body is not None else None,
            headers=headers,
        )
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", "")
        except json.JSONDecodeError:
            detail = resp.text
        console.print(
            f"[red]Guardrails request failed ({resp.status_code}): {detail}[/red]"
        )
        raise typer.Exit(1)
    return dict(resp.json())
