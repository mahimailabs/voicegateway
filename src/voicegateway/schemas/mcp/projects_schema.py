"""Input schemas for the projects MCP tool group."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from voicegateway.schemas.mcp import StrictMcpInput


class ListProjectsInput(StrictMcpInput):
    """Input for list_projects."""


class GetProjectInput(StrictMcpInput):
    """Input for get_project: requires project id."""

    project_id: str


class CreateProjectInput(StrictMcpInput):
    """Input for create_project: a framework-agnostic project.

    A project is an attribution + cost-control scope: a label plus a daily
    budget and what to do when it is exceeded. VoiceGateway no longer routes
    models, so the old stt/llm/tts_model + default_stack routing fields are
    gone.
    """

    project_id: str
    name: str
    description: str = ""
    daily_budget: float = Field(default=0.0, ge=0.0)
    budget_action: Literal["warn", "throttle", "block"] = "warn"
    tags: list[str] | None = None


class DeleteProjectInput(StrictMcpInput):
    """Input for delete_project: requires confirm flag."""

    project_id: str
    confirm: bool = False


__all__ = [
    "CreateProjectInput",
    "DeleteProjectInput",
    "GetProjectInput",
    "ListProjectsInput",
]
