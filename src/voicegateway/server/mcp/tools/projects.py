"""Project management tools — list/get/create/delete."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from voicegateway.core.auth import ADMIN_SCOPE
from voicegateway.schemas.mcp.projects_schema import (
    CreateProjectInput,
    DeleteProjectInput,
    GetProjectInput,
    ListProjectsInput,
)
from voicegateway.server.mcp.errors import (
    ConfirmationRequiredError,
    ProjectAlreadyExistsError,
    ProjectNotFoundError,
    ReadOnlyResourceError,
    ValidationError,
)
from voicegateway.server.mcp.tools.base import ToolDef, make_tool

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway


def _parse(model_cls: type, arguments: dict[str, Any]) -> Any:
    try:
        return model_cls(**arguments)
    except Exception as exc:
        raise ValidationError(str(exc)) from exc


LIST_PROJECTS_DOC = """List every project on the gateway with today's stats.

Use this to answer "What projects are configured?" or as the first step
before creating a new one. Returns both YAML-defined and GUI-managed
projects. Each entry includes today's spend and request count.

Args:
    (none)

Returns:
    A list of dicts: {id, name, description, daily_budget, budget_action,
    budget_status ("ok" | "warning" | "exceeded"), today_spend,
    today_requests, tags, default_stack, source}.
"""


async def _handle_list_projects(
    gateway: Gateway, arguments: dict[str, Any]
) -> dict[str, Any]:
    _parse(ListProjectsInput, arguments)

    projects: list[dict[str, Any]] = []
    managed_ids: set[str] = set()
    if gateway.storage is not None:
        for row in await gateway.storage.list_managed_projects():
            managed_ids.add(row["project_id"])

    for pid, pcfg in gateway.config.projects.items():
        today_spend = 0.0
        today_requests = 0
        if gateway.storage is not None:
            stats = await gateway.storage.get_project_stats(pid)
            today_spend = stats.get("cost_today", 0.0) or 0.0
            today_requests = int(stats.get("requests_today", 0) or 0)

        budget_status = gateway._budget_enforcer.get_budget_status(pid, today_spend)
        projects.append(
            {
                "id": pid,
                "name": pcfg.name,
                "description": pcfg.description,
                "daily_budget": pcfg.daily_budget,
                "budget_action": pcfg.budget_action,
                "budget_status": budget_status,
                "today_spend": today_spend,
                "today_requests": today_requests,
                "tags": list(pcfg.tags),
                "default_stack": pcfg.default_stack,
                "source": "db" if pid in managed_ids else "yaml",
            }
        )

    return {"projects": projects, "count": len(projects)}


GET_PROJECT_DOC = """Return full details for one project including cost trends.

Use this to answer "Show me tonys-pizza's details" or before deciding to
update/delete it. Provides config, today's stats, 7-day cost total, and
recent request summary.

Args:
    project_id: The id of the project to fetch.

Returns:
    A dict: {id, name, description, daily_budget, budget_action,
    budget_status, today_spend, today_requests, week_spend, week_requests,
    tags, default_stack, stt_model, llm_model, tts_model, source}.

Raises:
    PROJECT_NOT_FOUND if no project has that id.
"""


async def _handle_get_project(
    gateway: Gateway, arguments: dict[str, Any]
) -> dict[str, Any]:
    payload = _parse(GetProjectInput, arguments)

    pcfg = gateway.config.get_project(payload.project_id)
    if pcfg is None:
        raise ProjectNotFoundError(
            f"No project '{payload.project_id}'. Use list_projects to see options.",
            details={"project_id": payload.project_id},
        )

    today_stats: dict[str, Any] = {}
    week_summary: dict[str, Any] = {}
    managed_row: dict[str, Any] | None = None
    if gateway.storage is not None:
        today_stats = await gateway.storage.get_project_stats(payload.project_id)
        week_summary = await gateway.storage.get_cost_summary(
            "week", project=payload.project_id
        )
        managed_row = await gateway.storage.get_managed_project(payload.project_id)

    today_spend = today_stats.get("cost_today", 0.0) or 0.0
    budget_status = gateway._budget_enforcer.get_budget_status(
        payload.project_id, today_spend
    )

    stt_model = managed_row.get("stt_model") if managed_row else None
    llm_model = managed_row.get("llm_model") if managed_row else None
    tts_model = managed_row.get("tts_model") if managed_row else None

    return {
        "id": pcfg.id,
        "name": pcfg.name,
        "description": pcfg.description,
        "daily_budget": pcfg.daily_budget,
        "budget_action": pcfg.budget_action,
        "budget_status": budget_status,
        "today_spend": today_spend,
        "today_requests": int(today_stats.get("requests_today", 0) or 0),
        "week_spend": week_summary.get("total", 0.0),
        "week_requests": sum(
            int(d.get("requests", 0) or 0)
            for d in (week_summary.get("by_provider", {}) or {}).values()
        ),
        "tags": list(pcfg.tags),
        "default_stack": pcfg.default_stack,
        "stt_model": stt_model,
        "llm_model": llm_model,
        "tts_model": tts_model,
        "source": "db" if managed_row is not None else "yaml",
    }


CREATE_PROJECT_DOC = """Create a new project for cost tracking and budgeting.

A project is an attribution + cost-control scope: a label plus a daily
budget and what happens when it is exceeded. VoiceGateway meters the native
STT/LLM/TTS instances your agent attaches; it does not route models, so a
project carries no model configuration.

Args:
    project_id: Unique identifier (kebab-case recommended).
    name: Human-readable name.
    description: Optional long description.
    daily_budget: USD limit per day (0 = unlimited).
    budget_action: "warn" (default) logs when exceeded, "throttle" signals
        your agent to fall back to a cheaper model, "block" raises an error
        on future requests for the day.
    tags: Optional labels for grouping.

Returns:
    The created project record.

Raises:
    PROJECT_ALREADY_EXISTS if the id collides.
    VALIDATION_ERROR if cost tracking is disabled.
"""


async def _handle_create_project(
    gateway: Gateway, arguments: dict[str, Any]
) -> dict[str, Any]:
    payload = _parse(CreateProjectInput, arguments)

    if gateway.storage is None:
        raise ValidationError(
            "Creating projects requires cost_tracking.enabled=true.",
            details={"hint": "enable cost_tracking in voicegw.yaml"},
        )

    if payload.project_id in gateway.config.projects:
        raise ProjectAlreadyExistsError(
            f"Project '{payload.project_id}' already exists.",
            details={"project_id": payload.project_id},
        )

    tags = list(payload.tags or [])
    await gateway.storage.upsert_managed_project(
        project_id=payload.project_id,
        name=payload.name,
        description=payload.description,
        daily_budget=payload.daily_budget,
        budget_action=payload.budget_action,
        tags=tags,
    )
    await gateway.refresh_config()

    return {
        "project_id": payload.project_id,
        "name": payload.name,
        "description": payload.description,
        "daily_budget": payload.daily_budget,
        "budget_action": payload.budget_action,
        "tags": tags,
        "source": "db",
        "created": True,
        "created_at": time.time(),
    }


DELETE_PROJECT_DOC = """Delete a GUI-created project. Requires confirm=True.

DESTRUCTIVE. Returns a preview with total spend, request count, and last
activity so the agent can warn the user before confirming. Does NOT delete
request logs; only the project configuration is removed. Cannot delete
YAML-defined projects.

Args:
    project_id: The id to delete.
    confirm: Must be True to perform the delete.

Returns:
    Preview: {action: "would_delete", project_id, total_spend_usd,
    total_requests, last_activity}. Confirmed: {action: "deleted", project_id}.

Raises:
    PROJECT_NOT_FOUND if the id doesn't exist.
    READ_ONLY_RESOURCE if the project is defined in YAML.
    CONFIRMATION_REQUIRED if called without confirm=True.
"""


async def _handle_delete_project(
    gateway: Gateway, arguments: dict[str, Any]
) -> dict[str, Any]:
    payload = _parse(DeleteProjectInput, arguments)

    if payload.project_id not in gateway.config.projects:
        raise ProjectNotFoundError(
            f"No project '{payload.project_id}'.",
            details={"project_id": payload.project_id},
        )

    if gateway.storage is None:
        raise ReadOnlyResourceError(
            "Project deletion requires cost_tracking.enabled=true (managed storage).",
            details={"project_id": payload.project_id},
        )

    managed = await gateway.storage.get_managed_project(payload.project_id)
    if managed is None:
        raise ReadOnlyResourceError(
            f"Project '{payload.project_id}' is defined in voicegw.yaml and "
            "cannot be deleted via MCP.",
            details={"project_id": payload.project_id},
        )

    # Impact preview.
    all_time_summary = await gateway.storage.get_cost_summary(
        "all", project=payload.project_id
    )
    total_spend = all_time_summary.get("total", 0.0)
    total_requests = sum(
        int(d.get("requests", 0) or 0)
        for d in (all_time_summary.get("by_provider", {}) or {}).values()
    )
    recent = await gateway.storage.get_recent_requests(
        limit=1, project=payload.project_id
    )
    last_activity = recent[0]["timestamp"] if recent else None

    if not payload.confirm:
        raise ConfirmationRequiredError(
            f"Deleting project '{payload.project_id}' is destructive. "
            "Review the impact and call again with confirm=True.",
            details={
                "project_id": payload.project_id,
                "total_spend_usd": total_spend,
                "total_requests": total_requests,
                "last_activity": last_activity,
            },
        )

    await gateway.storage.delete_managed_project(payload.project_id)
    await gateway.refresh_config()

    return {
        "action": "deleted",
        "project_id": payload.project_id,
        "total_spend_usd": total_spend,
        "total_requests": total_requests,
    }


PROJECT_TOOLS: list[ToolDef] = [
    make_tool(
        "list_projects", LIST_PROJECTS_DOC, ListProjectsInput, _handle_list_projects
    ),
    make_tool("get_project", GET_PROJECT_DOC, GetProjectInput, _handle_get_project),
    make_tool(
        "create_project", CREATE_PROJECT_DOC, CreateProjectInput, _handle_create_project
    ),
    # Destructive: admin-only.
    make_tool(
        "delete_project",
        DELETE_PROJECT_DOC,
        DeleteProjectInput,
        _handle_delete_project,
        required_scope=ADMIN_SCOPE,
    ),
]
