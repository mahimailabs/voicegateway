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
    """Input for create_project: full project definition."""

    project_id: str
    name: str
    description: str = ""
    daily_budget: float = Field(default=0.0, ge=0.0)
    budget_action: Literal["warn", "throttle", "block"] = "warn"
    stt_model: str | None = None
    llm_model: str | None = None
    tts_model: str | None = None
    default_stack: str | None = None
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
