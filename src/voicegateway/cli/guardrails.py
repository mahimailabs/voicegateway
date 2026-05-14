"""``voicegw guardrails`` command group."""

from __future__ import annotations

import json

import typer

from voicegateway.cli._app import app, console
from voicegateway.core.constants import DEFAULT_DASHBOARD_URL
from voicegateway.schemas.guardrail_policy_schema import (
    GUARDRAIL_CATEGORIES,
    GuardrailPolicy,
)
from voicegateway.utils.cli.guardrails import _request

guardrails_app = typer.Typer(
    name="guardrails",
    help="Inspect and update per-project LLM-side guardrail policies.",
    no_args_is_help=True,
)
app.add_typer(guardrails_app, name="guardrails")


@guardrails_app.command("show")
def show_cmd(
    project: str = typer.Option(..., "--project", "-p", help="Project id."),
    dashboard_url: str = typer.Option(
        DEFAULT_DASHBOARD_URL, "--dashboard-url", help="Dashboard base URL."
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    """Print the current guardrail policy for a project."""
    payload = _request(
        "GET",
        f"/api/projects/{project}/guardrails",
        dashboard_url=dashboard_url,
    )
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return
    policy = GuardrailPolicy.from_raw(payload.get("policy"))
    console.print(f"[bold]Project {project}[/bold] guardrails")
    console.print(f"  Enabled: {policy.enabled}")
    for category in GUARDRAIL_CATEGORIES:
        console.print(f"  {category}: {policy.categories.get(category, 'off')}")


@guardrails_app.command("set")
def set_cmd(
    project: str = typer.Option(..., "--project", "-p", help="Project id."),
    category: str = typer.Option(..., "--category", "-c", help="Guardrail category."),
    action: str = typer.Option(
        ..., "--action", "-a", help="redact, block, alert, or off."
    ),
    dashboard_url: str = typer.Option(
        DEFAULT_DASHBOARD_URL, "--dashboard-url", help="Dashboard base URL."
    ),
) -> None:
    """Set one category action, preserving the other categories."""
    current = _request(
        "GET",
        f"/api/projects/{project}/guardrails",
        dashboard_url=dashboard_url,
    )
    policy = GuardrailPolicy.from_raw(current.get("policy")).to_storage_dict()
    policy["enabled"] = True
    categories = dict(policy.get("categories") or {})
    categories[category] = action
    policy["categories"] = categories
    validated = GuardrailPolicy.from_raw(policy).to_storage_dict()
    updated = _request(
        "POST",
        f"/api/projects/{project}/guardrails",
        dashboard_url=dashboard_url,
        body=validated,
    )
    console.print(
        f"[bold]Project {project}[/bold] guardrail {category} set to "
        f"{updated['policy']['categories'].get(category, action)}."
    )


@guardrails_app.command("clear")
def clear_cmd(
    project: str = typer.Option(..., "--project", "-p", help="Project id."),
    dashboard_url: str = typer.Option(
        DEFAULT_DASHBOARD_URL, "--dashboard-url", help="Dashboard base URL."
    ),
) -> None:
    """Disable all guardrails for a project."""
    policy = GuardrailPolicy.disabled().to_storage_dict()
    _request(
        "POST",
        f"/api/projects/{project}/guardrails",
        dashboard_url=dashboard_url,
        body=policy,
    )
    console.print(f"[bold]Project {project}[/bold] guardrails cleared.")


@guardrails_app.command("dry-run")
def dry_run_cmd(
    project: str = typer.Option(..., "--project", "-p", help="Project id."),
    dashboard_url: str = typer.Option(
        DEFAULT_DASHBOARD_URL, "--dashboard-url", help="Dashboard base URL."
    ),
) -> None:
    """Print the composed guardrail prompt block for inspection."""
    payload = _request(
        "GET",
        f"/api/projects/{project}/guardrails",
        dashboard_url=dashboard_url,
    )
    policy = GuardrailPolicy.from_raw(payload.get("policy"))
    block = compose_block(policy)
    console.print(block or "No active guardrails for this project.")
